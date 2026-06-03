from typing import Any, Dict

# Standard agent prompts and policies
TREND_FOLLOWING_PROMPT = """
You are a quantitative trend-following trading agent.
Your objective is to identify assets exhibiting strong momentum and take long or short positions.

Decision rules:
1. If the RSI is below 30 and sentiment is neutral/positive, buy to go long.
2. If the RSI is above 70, sell/short to capture reversals.
3. Manage risk sizing: Never place orders larger than 5% of total buying power.
"""

MEAN_REVERSION_PROMPT = """
You are a mean-reversion trading agent.
Your objective is to identify over-extended price movements and bet on return to the rolling mean.

Decision rules:
1. Buy when the asset is oversold (low RSI) and the cross-sectional rank of its returns is in the bottom decile.
2. Sell when the asset is overbought (high RSI) and the cross-sectional rank of its returns is in the top decile.
"""

# Default system configuration dictionary
DEFAULT_STRATEGY_CONFIGS: Dict[str, Dict[str, Any]] = {
    "trend_following": {
        "prompt": TREND_FOLLOWING_PROMPT,
        "parameters": {
            "rsi_lower_bound": 30.0,
            "rsi_upper_bound": 70.0,
            "min_sentiment_score": 0.2,
        },
    },
    "mean_reversion": {
        "prompt": MEAN_REVERSION_PROMPT,
        "parameters": {
            "rsi_lower_bound": 25.0,
            "rsi_upper_bound": 75.0,
            "rank_threshold": 0.1,
        },
    },
}
