"""Seat counts read from Google's subscription JSON (the 300 cap must never be taken as the count)."""

from __future__ import annotations

from app.adapters.google.google_reseller import _parse_subscription


def _sub(plan_name, seats, commitment=False):
    return _parse_subscription({
        "customerId": "C1", "subscriptionId": "S1", "skuId": "1010020027", "status": "ACTIVE",
        "plan": {"planName": plan_name, "isCommitmentPlan": commitment},
        "seats": seats,
    })


def test_annual_uses_number_of_seats_not_the_300_cap():
    sub = _sub("ANNUAL_MONTHLY_PAY", {"numberOfSeats": 5, "maximumNumberOfSeats": 300, "licensedNumberOfSeats": 5}, True)
    assert sub.seats.number_of_seats == 5


def test_plain_annual_plan_name_also_uses_number_of_seats():
    assert _sub("ANNUAL", {"numberOfSeats": 1, "maximumNumberOfSeats": 300}).seats.number_of_seats == 1


def test_commitment_flag_alone_is_enough():
    assert _sub("SOMETHING_NEW", {"numberOfSeats": 7, "maximumNumberOfSeats": 300}, True).seats.number_of_seats == 7


def test_annual_without_number_falls_back_to_licensed_never_the_cap():
    sub = _sub("ANNUAL_YEARLY_PAY", {"maximumNumberOfSeats": 300, "licensedNumberOfSeats": 3}, True)
    assert sub.seats.number_of_seats == 3


def test_flexible_uses_maximum_number_of_seats():
    assert _sub("FLEXIBLE", {"maximumNumberOfSeats": 10, "licensedNumberOfSeats": 2}).seats.number_of_seats == 10


def test_trial_uses_maximum_number_of_seats():
    assert _sub("TRIAL", {"maximumNumberOfSeats": 3}).seats.number_of_seats == 3


def test_flexible_falls_back_to_number_of_seats():
    assert _sub("FLEXIBLE", {"numberOfSeats": 4}).seats.number_of_seats == 4
