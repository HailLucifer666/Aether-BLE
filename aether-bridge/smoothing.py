"""Signal smoothing utilities shared across scanner, aggregator, and bridge."""

EMA_ALPHA = 0.3


def apply_ema(previous_smoothed: float | None, raw: float, alpha: float = EMA_ALPHA) -> float:
    """Exponential moving average: smoothed = alpha*raw + (1-alpha)*prev.

    On the first sample (no previous value) the smoothed value equals raw.
    """
    if previous_smoothed is None:
        return raw
    return alpha * raw + (1 - alpha) * previous_smoothed
