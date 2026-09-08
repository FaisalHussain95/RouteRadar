"""Domain models: the validated shapes that cross module boundaries.

Money is `Decimal`. Pydantic would happily coerce a float into a Decimal, and a float that
came out of a JSON parser is exactly how `611.9999999` ends up in a fare table, so `Money`
rejects floats outright while still accepting the int and string forms providers emit.

`Itinerary` is what a provider returns for one search; `FareObservation` is the same thing
stamped with the day it was seen, flattened to the columns of the `fare_observation`
table. Keeping them separate means the provider protocol never has to know about
observation dates, and the DB layer never has to unpack a nested route.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)

Origin = Literal["CDG", "ORY"]
Destination = Literal["ISB", "LHE", "SKT"]
Cabin = Literal["economy", "premium_economy", "business", "first"]


def _reject_float(value: object) -> object:
    if isinstance(value, float):
        raise ValueError("float is not accepted for money; pass a Decimal, int or string")
    return value


Money = Annotated[Decimal, BeforeValidator(_reject_float), Field(ge=0, decimal_places=2)]
Multiplier = Annotated[Decimal, BeforeValidator(_reject_float), Field(gt=0, decimal_places=2)]


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


AwareDatetime = Annotated[datetime, AfterValidator(_require_aware)]


class Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Route(Base):
    origin: Origin
    destination: Destination


class Itinerary(Base):
    """One priced itinerary as returned by a FareProvider, before scope filtering."""

    route: Route
    departure_date: date
    carrier: str = Field(min_length=2, max_length=3, description="Marketing carrier IATA code")
    flight_numbers: list[str] = Field(min_length=1)
    stops: int = Field(ge=0)
    layover_minutes: int = Field(ge=0)
    duration_minutes: int = Field(gt=0)
    cabin: Cabin
    price_eur: Money
    provider: str
    raw_ref: str = Field(description="Provider-side handle for the raw result, for debugging")


class FareObservation(Base):
    """One row of `fare_observation`: an Itinerary seen on a given day."""

    observed_on: date
    departure_date: date
    origin: Origin
    destination: Destination
    carrier: str = Field(min_length=2, max_length=3)
    flight_numbers: list[str] = Field(min_length=1)
    stops: int = Field(ge=0)
    layover_minutes: int = Field(ge=0)
    duration_minutes: int = Field(gt=0)
    price_eur: Money
    provider: str
    raw_ref: str

    @classmethod
    def from_itinerary(cls, itinerary: Itinerary, *, observed_on: date) -> Self:
        return cls(
            observed_on=observed_on,
            departure_date=itinerary.departure_date,
            origin=itinerary.route.origin,
            destination=itinerary.route.destination,
            carrier=itinerary.carrier,
            flight_numbers=itinerary.flight_numbers,
            stops=itinerary.stops,
            layover_minutes=itinerary.layover_minutes,
            duration_minutes=itinerary.duration_minutes,
            price_eur=itinerary.price_eur,
            provider=itinerary.provider,
            raw_ref=itinerary.raw_ref,
        )

    @property
    def flight_numbers_key(self) -> str:
        """The stored form of `flight_numbers`: '+'-joined so it can sit in a primary key."""
        return "+".join(self.flight_numbers)

    @property
    def key(self) -> tuple[date, date, str, str, str, str]:
        """The primary key of `fare_observation`, in column order."""
        return (
            self.observed_on,
            self.departure_date,
            self.origin,
            self.destination,
            self.carrier,
            self.flight_numbers_key,
        )


class CalendarTag(Base):
    """A cultural/calendar window applying to one departure date."""

    date: date
    tag: str
    multiplier_low: Multiplier
    multiplier_high: Multiplier

    @model_validator(mode="after")
    def _low_not_above_high(self) -> Self:
        if self.multiplier_low > self.multiplier_high:
            raise ValueError("multiplier_low must not exceed multiplier_high")
        return self


class NewsEvent(Base):
    event_date: date
    category: str
    severity: int = Field(ge=1, le=3)
    headline: str
    source_url: str
    dedupe_key: str
    impact_note: str | None = None


class IngestRun(Base):
    run_at: AwareDatetime
    provider: str
    queries: int = Field(ge=0)
    rows_kept: int = Field(ge=0)
    rows_dropped: int = Field(ge=0)
    error: str | None = None
