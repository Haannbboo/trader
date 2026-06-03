import sys

from loguru import logger
from prometheus_client import Counter, Gauge, Histogram


# Initialize default logger format and destinations
def setup_logging(level: str = "INFO") -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level=level,
    )
    logger.info(f"Logging setup complete. Level set to: {level}")


# Standard trading metrics
ORDER_COUNTER = Counter(
    "trader_orders_submitted_total",
    "Total number of orders submitted to brokers",
    ["symbol", "side", "type"],
)

ORDER_FILL_COUNTER = Counter(
    "trader_orders_filled_total",
    "Total number of orders successfully filled",
    ["symbol", "side"],
)

POSITION_GAUGE = Gauge(
    "trader_position_size", "Current position size (shares/contracts)", ["symbol"]
)

PORTFOLIO_VALUE_GAUGE = Gauge(
    "trader_portfolio_value_dollars", "Total portfolio value in dollars"
)

LATENCY_HISTOGRAM = Histogram(
    "trader_processing_latency_seconds",
    "Time taken to process ticks and make trading decisions",
    ["step"],
)
