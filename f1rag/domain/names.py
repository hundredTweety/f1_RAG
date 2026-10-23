"""F1 고유명사의 한국어 표기 (데이터 준비 영역, 도메인 전용).

Jolpica-F1 API는 영어 이름만 주므로, 한국어 질문이 항목 텍스트와 잘 맞도록 한국어 표기를 함께 넣는다.
목록에 없는 이름은 영어 그대로 쓴다. 표기는 한국어 위키백과에서 흔히 쓰는 형태를 따랐다.
"""

GRAND_PRIX_KO = {
    "Abu Dhabi Grand Prix": "아부다비 그랑프리",
    "Australian Grand Prix": "호주 그랑프리",
    "Austrian Grand Prix": "오스트리아 그랑프리",
    "Azerbaijan Grand Prix": "아제르바이잔 그랑프리",
    "Bahrain Grand Prix": "바레인 그랑프리",
    "Belgian Grand Prix": "벨기에 그랑프리",
    "British Grand Prix": "영국 그랑프리",
    "Canadian Grand Prix": "캐나다 그랑프리",
    "Chinese Grand Prix": "중국 그랑프리",
    "Dutch Grand Prix": "네덜란드 그랑프리",
    "Emilia Romagna Grand Prix": "에밀리아로마냐 그랑프리",
    "French Grand Prix": "프랑스 그랑프리",
    "Hungarian Grand Prix": "헝가리 그랑프리",
    "Italian Grand Prix": "이탈리아 그랑프리",
    "Japanese Grand Prix": "일본 그랑프리",
    "Las Vegas Grand Prix": "라스베이거스 그랑프리",
    "Mexico City Grand Prix": "멕시코시티 그랑프리",
    "Miami Grand Prix": "마이애미 그랑프리",
    "Monaco Grand Prix": "모나코 그랑프리",
    "Portuguese Grand Prix": "포르투갈 그랑프리",
    "Qatar Grand Prix": "카타르 그랑프리",
    "Russian Grand Prix": "러시아 그랑프리",
    "Saudi Arabian Grand Prix": "사우디아라비아 그랑프리",
    "Singapore Grand Prix": "싱가포르 그랑프리",
    "Spanish Grand Prix": "스페인 그랑프리",
    "Styrian Grand Prix": "슈타이어마르크 그랑프리",
    "São Paulo Grand Prix": "상파울루 그랑프리",
    "Sao Paulo Grand Prix": "상파울루 그랑프리",
    "Turkish Grand Prix": "튀르키예 그랑프리",
    "United States Grand Prix": "미국 그랑프리",
}

COUNTRY_KO = {
    "Australia": "호주",
    "Austria": "오스트리아",
    "Azerbaijan": "아제르바이잔",
    "Bahrain": "바레인",
    "Belgium": "벨기에",
    "Brazil": "브라질",
    "Canada": "캐나다",
    "China": "중국",
    "France": "프랑스",
    "Hungary": "헝가리",
    "Italy": "이탈리아",
    "Japan": "일본",
    "Mexico": "멕시코",
    "Monaco": "모나코",
    "Netherlands": "네덜란드",
    "Portugal": "포르투갈",
    "Qatar": "카타르",
    "Russia": "러시아",
    "Saudi Arabia": "사우디아라비아",
    "Singapore": "싱가포르",
    "Spain": "스페인",
    "Turkey": "튀르키예",
    "UAE": "아랍에미리트",
    "United Arab Emirates": "아랍에미리트",
    "UK": "영국",
    "United Kingdom": "영국",
    "USA": "미국",
    "United States": "미국",
}

# 성(family name) 기준. 2021~2025 시즌 결선 출전 드라이버
DRIVER_KO = {
    "Albon": "알렉산더 알본",
    "Alonso": "페르난도 알론소",
    "Antonelli": "안드레아 키미 안토넬리",
    "Bearman": "올리버 베어먼",
    "Bortoleto": "가브리엘 보르톨레토",
    "Bottas": "발테리 보타스",
    "Colapinto": "프랑코 콜라핀토",
    "de Vries": "닉 더프리스",
    "Doohan": "잭 두한",
    "Gasly": "피에르 가슬리",
    "Giovinazzi": "안토니오 조비나치",
    "Hadjar": "이삭 하자르",
    "Hamilton": "루이스 해밀턴",
    "Hülkenberg": "니코 휠켄베르크",
    "Hulkenberg": "니코 휠켄베르크",
    "Kubica": "로베르트 쿠비차",
    "Latifi": "니콜라스 라티피",
    "Lawson": "리엄 로슨",
    "Leclerc": "샤를 르클레르",
    "Magnussen": "케빈 마그누센",
    "Mazepin": "니키타 마제핀",
    "Norris": "랜도 노리스",
    "Ocon": "에스테반 오콘",
    "Pérez": "세르히오 페레스",
    "Perez": "세르히오 페레스",
    "Piastri": "오스카 피아스트리",
    "Räikkönen": "키미 라이코넨",
    "Raikkonen": "키미 라이코넨",
    "Ricciardo": "대니얼 리카도",
    "Russell": "조지 러셀",
    "Sainz": "카를로스 사인츠",
    "Sargeant": "로건 사전트",
    "Schumacher": "믹 슈마허",
    "Stroll": "랜스 스트롤",
    "Tsunoda": "쓰노다 유키",
    "Verstappen": "막스 베르스타펜",
    "Vettel": "제바스티안 페텔",
    "Zhou": "저우관위",
}

CONSTRUCTOR_KO = {
    "Alfa Romeo": "알파 로메오",
    "AlphaTauri": "알파타우리",
    "Alpine F1 Team": "알핀",
    "Aston Martin": "애스턴 마틴",
    "Ferrari": "페라리",
    "Haas F1 Team": "하스",
    "McLaren": "맥라렌",
    "Mercedes": "메르세데스",
    "RB F1 Team": "RB",
    "Red Bull": "레드불",
    "Sauber": "자우버",
    "Williams": "윌리엄스",
}


def gp_ko(name_en: str) -> str:
    return GRAND_PRIX_KO.get(name_en, name_en)


def country_ko(country: str) -> str:
    return COUNTRY_KO.get(country, country)


def driver_ko(given: str, family: str) -> str:
    return DRIVER_KO.get(family, f"{given} {family}".strip())


def constructor_ko(name: str) -> str:
    return CONSTRUCTOR_KO.get(name, name)
