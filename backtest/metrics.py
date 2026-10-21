import pandas as pd


def summarize_trades(
    trades: pd.DataFrame,
) -> dict:

    if trades.empty:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "session_closes": 0,
            "win_rate": 0.0,
            "total_r": 0.0,
            "average_r": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
        }

    wins = (
        trades["r_multiple"] > 0
    ).sum()

    losses = (
        trades["r_multiple"] < 0
    ).sum()

    session_closes = (
        trades["outcome"] == "SESSION_CLOSE"
    ).sum()

    positive_r = trades.loc[
        trades["r_multiple"] > 0,
        "r_multiple",
    ].sum()

    negative_r = abs(
        trades.loc[
            trades["r_multiple"] < 0,
            "r_multiple",
        ].sum()
    )

    if negative_r > 0:
        profit_factor = (
            positive_r / negative_r
        )
    else:
        profit_factor = float("inf")


    max_drawdown = calculate_max_drawdown(
        trades["r_multiple"]
    )

    return {
        "trades": len(trades),
        "wins": int(wins),
        "losses": int(losses),
        "session_closes": int(session_closes),
        "win_rate": (
            wins / len(trades)
        ),
        "total_r": trades["r_multiple"].sum(),
        "average_r": trades["r_multiple"].mean(),
        "profit_factor": profit_factor,
        "max_drawdown": max_drawdown,
    }

def calculate_max_drawdown(
    r_series: pd.Series,
) -> float:
    """
    Calculate maximum drawdown in R.

    Example:
        cumulative R:
        1, 3, 2, 4, 1

        peak = 4
        trough = 1
        max drawdown = -3R
    """

    cumulative = r_series.cumsum()

    running_peak = cumulative.cummax()

    drawdown = cumulative - running_peak

    return float(drawdown.min())
