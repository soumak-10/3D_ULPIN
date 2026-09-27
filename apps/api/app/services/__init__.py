"""Domain services.

This is where a unit of work lives: load, check scope, mutate, commit. Services
own the transaction boundary and the row-level authorisation decisions that the
routing layer cannot make because it has not loaded the row.
"""
