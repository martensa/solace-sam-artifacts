"""Scraper package for the price comparison agent."""

from .base import BaseScraper
from .idealo import IdealoScraper
from .geizhals import GeizhalsScraper
from .google_shopping import GoogleShoppingScraper

__all__ = ["BaseScraper", "IdealoScraper", "GeizhalsScraper", "GoogleShoppingScraper"]
