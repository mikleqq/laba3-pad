"""Shared infrastructure for ShopHub microservices.

This package contains only cross-cutting technical concerns (logging, error
handling, security helpers, messaging and database plumbing). It contains no
business logic: every service owns its own domain model, API and database.
"""

__version__ = "1.0.0"
