"""Read-only extraction of xBase (dBase / FoxPro / Visual FoxPro) databases.

Many locally installed practice-management and EHR systems store their data
as .DBF tables with .FPT/.DBT memo files and .CDX indexes. This package
exports every table to CSV, SQLite and PostgreSQL, recovers memo text,
deleted-flagged records and attachments, and reconciles every value against
an independent reader.
"""
__version__ = "0.1.0"
