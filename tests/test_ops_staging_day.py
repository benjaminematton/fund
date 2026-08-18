"""ops/staging-day.sh — the guard that stops a rehearsal from moving the real fund.

A staging day that shares production's Alpaca account or database is worse than
no rehearsal: its orders change production's positions and buying power, and
nothing downstream can detect it. These tests pin the refusals.
"""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops" / "staging-day.sh"

PROD_KEYS = ("PKPROD00000000000001", "prodsecret")
STG_KEYS = ("PKSTG000000000000001", "stgsecret")


def _env_file(path: Path, key: str, secret: str, db: str, paper: str = "true") -> Path:
    path.write_text(
        f"ANTHROPIC_API_KEY=sk-ant-x\n"
        f"ALPACA_API_KEY={key}\n"
        f"ALPACA_SECRET_KEY={secret}\n"
        f"ALPACA_PAPER_TRADE={paper}\n"
        f"FUND_DB={db}\n"
    )
    return path


def _fake_curl(tmp_path: Path, mapping: dict[str, str]) -> Path:
    """Fake curl answering an account number keyed off the APCA-API-KEY-ID header."""
    cases = "\n".join(
        f'    *{k}*) printf \'{{"account_number":"{v}"}}\' ;;' for k, v in mapping.items()
    )
    fake = tmp_path / "curl"
    fake.write_text(
        "#!/bin/sh\n"
        'args="$*"\n'
        "case \"$args\" in\n" + cases + "\n"
        '    *) printf \'{}\' ;;\n'
        "esac\n"
    )
    fake.chmod(0o755)
    return fake


def _run(tmp_path, prod_db="/var/lib/fund/fund.sqlite",
         stg_db="/var/lib/fund/staging/fund.sqlite",
         stg_key=STG_KEYS[0], accounts=None, paper="true"):
    prod = _env_file(tmp_path / "prod-env", *PROD_KEYS, prod_db)
    stg = _env_file(tmp_path / "stg-env", stg_key, STG_KEYS[1], stg_db, paper)
    accounts = accounts or {PROD_KEYS[0]: "PA_PROD", STG_KEYS[0]: "PA_STAGING"}
    env = {
        **os.environ,
        "FUND_PROD_ENV": str(prod),
        "FUND_STAGING_ENV": str(stg),
        "STAGING_CURL": str(_fake_curl(tmp_path, accounts)),
        "STAGING_GUARD_ONLY": "1",
    }
    return subprocess.run([str(SCRIPT)], capture_output=True, text=True, env=env)


def test_allows_distinct_account_and_db(tmp_path):
    proc = _run(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "PA_PROD" in proc.stdout and "PA_STAGING" in proc.stdout


def test_refuses_when_both_envs_resolve_to_the_same_account(tmp_path):
    """The hazard this script exists for: one paper account, two environments."""
    proc = _run(tmp_path, accounts={PROD_KEYS[0]: "PA_SAME", STG_KEYS[0]: "PA_SAME"})
    assert proc.returncode != 0
    assert "same Alpaca account" in proc.stderr


def test_refuses_when_fund_db_is_shared(tmp_path):
    proc = _run(tmp_path, stg_db="/var/lib/fund/fund.sqlite")
    assert proc.returncode != 0
    assert "share FUND_DB" in proc.stderr


def test_refuses_when_staging_is_not_paper(tmp_path):
    """Invariant 1 holds in staging too."""
    proc = _run(tmp_path, paper="false")
    assert proc.returncode != 0
    assert "ALPACA_PAPER_TRADE" in proc.stderr


def test_refuses_when_credentials_do_not_resolve(tmp_path):
    """An unreachable or rejected key must stop the run, not fall through to a
    comparison of two empty strings — which would compare equal and read as the
    shared-account refusal for entirely the wrong reason."""
    proc = _run(tmp_path, accounts={PROD_KEYS[0]: "PA_PROD"})  # staging answers {}
    assert proc.returncode != 0
    assert "staging credentials did not resolve" in proc.stderr


def test_missing_env_file_fails_fast(tmp_path):
    env = {**os.environ, "FUND_PROD_ENV": str(tmp_path / "nope"),
           "FUND_STAGING_ENV": str(tmp_path / "also-nope")}
    proc = subprocess.run([str(SCRIPT)], capture_output=True, text=True, env=env)
    assert proc.returncode != 0
    assert "cannot read" in proc.stderr


def test_reset_refuses_when_the_guard_fails(tmp_path):
    """staging-reset liquidates positions. If the guard cannot confirm the
    account is distinct, it must not reach the DELETE — a wrong account here
    closes the real fund's book."""
    prod = _env_file(tmp_path / "prod-env", *PROD_KEYS, "/var/lib/fund/fund.sqlite")
    stg = _env_file(tmp_path / "stg-env", STG_KEYS[0], STG_KEYS[1],
                    "/var/lib/fund/staging/fund.sqlite")
    curl_log = tmp_path / "curl-calls.txt"
    fake = tmp_path / "curl"
    fake.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> {curl_log}\n'
        "printf '{\"account_number\":\"PA_SAME\"}'\n"   # both resolve the same
    )
    fake.chmod(0o755)
    proc = subprocess.run(
        [str(ROOT / "ops" / "staging-reset.sh")], capture_output=True, text=True,
        env={**os.environ, "FUND_PROD_ENV": str(prod), "FUND_STAGING_ENV": str(stg),
             "STAGING_CURL": str(fake)})
    assert proc.returncode != 0
    assert "REFUSING" in proc.stderr
    assert "Nothing was liquidated" in proc.stderr
    assert "DELETE" not in curl_log.read_text(), "reset issued a DELETE despite the guard failing"
