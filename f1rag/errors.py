"""화면에 보여 줄 수 있는 오류.

이 클래스들의 message 는 사용자에게 그대로 보여도 되는 안내 문구다.
그 밖의 예외는 화면에 내용을 노출하지 않고 오류 번호만 보여 준다.
"""


class UserFacingError(Exception):
    """사용자에게 그대로 보여 줄 안내 문구를 가진 오류."""

    code = "error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class MissingApiKeyError(UserFacingError):
    code = "missing_api_key"

    def __init__(self):
        super().__init__(
            "OpenAI API 키가 설정되지 않았습니다. 프로젝트 폴더의 .env 파일에 "
            "OPENAI_API_KEY 를 입력한 뒤 앱을 다시 실행하세요."
        )


class MissingContactError(UserFacingError):
    code = "missing_contact"

    def __init__(self):
        super().__init__(
            "위키백과 요청에 필요한 연락처가 없습니다. .env 파일에 WIKI_CONTACT(이메일)를 입력하세요."
        )


class EmptyIndexError(UserFacingError):
    code = "empty_index"

    def __init__(self):
        super().__init__(
            "경기 메타데이터 색인이 비어 있습니다. 터미널에서 "
            "`python scripts/build_races.py` 와 `python scripts/index_items.py` 를 차례로 실행하세요."
        )


class UnknownItemError(UserFacingError):
    code = "unknown_item"

    def __init__(self, item_id: str):
        super().__init__(f"선택한 경기({item_id})를 색인에서 찾을 수 없습니다. 목록에서 다시 선택하세요.")


class SourceError(Exception):
    """외부 API 호출이 재시도 후에도 실패했을 때."""
