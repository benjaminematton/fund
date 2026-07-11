"""In-memory paper broker for offline tests: enforces client_order_id
uniqueness exactly like Alpaca (422 on duplicates — contracts §5.1) and
fills market orders instantly at frozen fixture prices."""

from __future__ import annotations


class FakeAlpaca:
    def __init__(self, prices: dict[str, float],
                 fill_prices: dict[str, float] | None = None) -> None:
        self.prices = dict(prices)
        self.fill_prices = dict(fill_prices or {})
        self.orders: dict[str, dict] = {}
        self.place_attempts: list[dict] = []

    def place_order(self, args: dict) -> dict:
        self.place_attempts.append(dict(args))
        coid = args["client_order_id"]
        if coid in self.orders:
            return {"error": "client_order_id must be unique", "status_code": 422}
        symbol = args["symbol"]
        px = self.fill_prices.get(symbol, self.prices[symbol])
        order = {
            "id": f"alp-{len(self.orders) + 1:04d}",
            "client_order_id": coid,
            "symbol": symbol,
            "side": args["side"],
            "qty": args["qty"],
            "status": "filled",
            "filled_qty": args["qty"],
            "filled_avg_price": px,
            "order_class": args.get("order_class", "simple"),
            "stop_loss": args.get("stop_loss"),
        }
        self.orders[coid] = order
        return dict(order)

    def get_order_by_client_order_id(self, coid: str) -> dict | None:
        o = self.orders.get(coid)
        return dict(o) if o else None
