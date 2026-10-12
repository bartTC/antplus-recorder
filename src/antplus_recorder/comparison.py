"""Power difference of a meter against a reference meter."""

from dataclasses import dataclass

# Watts; below this the reference is coasting and a percentage says nothing
MIN_REFERENCE_POWER = 20


def difference(
    value: float | None, reference: float | None
) -> tuple[float, float] | None:
    """Watts and percent `value` is above (or below) `reference`.

    Returns None if either is missing, or the reference is below
    MIN_REFERENCE_POWER, where a few watts of noise would be a huge percentage.
    """
    if value is None or reference is None or reference < MIN_REFERENCE_POWER:
        return None
    return value - reference, (value - reference) / reference * 100


@dataclass
class Lap:
    """Work of a meter and of the reference, summed over the seconds both measured.

    Comparing the totals weighs each second by its power, which makes it the
    steadiest number for correcting one meter against the other.
    """

    work: float = 0.0
    reference_work: float = 0.0
    seconds: int = 0

    def add(self, power: float, reference_power: float) -> None:
        """Count one second in which both meters measured."""
        self.work += power
        self.reference_work += reference_power
        self.seconds += 1

    @property
    def percent(self) -> float | None:
        """The meter's lap work above (or below) the reference in percent, None before any."""
        if not self.reference_work:
            return None
        return (self.work - self.reference_work) / self.reference_work * 100
