"""Pydantic contracts.

The request models are the first line of validation: a payload that cannot
express an invalid state never reaches the service. Response models exist partly
to *withhold* — password hashes and national identity numbers have no field to
be serialised into.
"""
