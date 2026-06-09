from app.discovery.discovery import discover
from app.discovery.models import Criteria
from app.discovery.ranking import rank


def main() -> None:
    criteria = Criteria(
        population="Boston University students with anxiety",
        location="Boston",
        in_person=True,
        age_min=18,
        age_max=30,
        sample_size=100,
        study_topic="anxiety and student mental health",
    )
    result = discover(criteria)
    result.channels = rank(result.channels, criteria)
    print(result.model_dump_json(indent=2) if hasattr(result, "model_dump_json") else result.json(indent=2))


if __name__ == "__main__":
    main()

