"""Questionnaires persistence models."""

from sqlalchemy import Boolean
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import JSON
from sqlalchemy import String
from datetime import datetime
from sqlalchemy.orm import relationship
from .base import Base

class Question(Base):
    __tablename__ = "questions"

    id = Column(Integer, primary_key=True, index=True)
    survey_id = Column(Integer, ForeignKey("surveys.id"), nullable=False)
    question_text = Column(String, nullable=False)
    question_type = Column(String, nullable=False)
    # single / multiple / text / scale / dropdown
    options = Column(JSON, nullable=True)
    is_required = Column(Boolean, default=True)
    order_index = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    survey = relationship("Survey", back_populates="questions")
    answers = relationship("Answer", back_populates="question")


class Answer(Base):
    __tablename__ = "answers"

    id = Column(Integer, primary_key=True, index=True)
    response_id = Column(Integer, ForeignKey("responses.id"), nullable=False)
    question_id = Column(Integer, ForeignKey("questions.id"), nullable=False)
    answer_value = Column(JSON, nullable=False)
    # single_choice: "A"
    # multiple_choice: ["A", "B"]
    # text: "I think..."
    # scale: 4
    created_at = Column(DateTime, default=datetime.utcnow)

    response = relationship("Response", back_populates="answers")
    question = relationship("Question", back_populates="answers")
