"""The single declarative registry shared by every Insighta table."""
from sqlalchemy.orm import declarative_base

Base = declarative_base()
