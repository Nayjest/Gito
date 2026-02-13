"""
Utilities specific to Python general functionality.
"""
import logging
from dataclasses import fields, is_dataclass


def filter_kwargs(cls, kwargs, log_warnings=True):
    """
    Filters the keyword arguments to only include those that are fields of the given dataclass.
    Args:
        cls: The dataclass type to filter against.
        kwargs: A dictionary of keyword arguments.
        log_warnings: If True, logs warnings for fields not in the dataclass.
    Returns:
        A dictionary containing only the fields that are defined in the dataclass.
    """
    if not is_dataclass(cls):
        raise TypeError(f"{cls.__name__} is not a dataclass or pydantic dataclass")

    cls_fields = {f.name for f in fields(cls)}
    filtered = {}
    for k, v in kwargs.items():
        if k in cls_fields:
            filtered[k] = v
        else:
            if log_warnings:
                logging.warning(
                    f"Warning: field '{k}' not in {cls.__name__}, dropping."
                )
    return filtered
