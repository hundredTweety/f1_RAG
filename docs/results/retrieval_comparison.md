# 검색 설정 비교 (MMR 검색, Top-k)

실행: 2026-10-22 14:51 / fetch_k 30, mmr_lambda 0.5

## 2021 R22 아부다비 그랑프리: 마지막 랩 세이프티카 재출발 절차는 규정에 맞았나?

### 유사도 검색 vs MMR (Top-k 6)

| 방식 | 서로 다른 문서 수 | 근거 간 평균 유사도 | 출처 종류 |
| --- | --- | --- | --- |
| similarity | 2 | 0.629 | 위키백과 6, FIA 스포팅 규정 3 |
| mmr | 3 | 0.520 | 위키백과 6, FIA 스포팅 규정 3 |

| 순위 | 유사도 검색 | MMR |
| --- | --- | --- |
| 1 | 2021 아부다비 그랑프리 | 2021 아부다비 그랑프리 |
| 2 | 2021 Abu Dhabi Grand Prix › Final laps | 2021 Abu Dhabi Grand Prix › Final laps |
| 3 | 2021 Abu Dhabi Grand Prix › Final laps | 2021 Abu Dhabi Grand Prix › Mercedes' protest |
| 4 | 2021 Abu Dhabi Grand Prix › Controversies surrounding safety car period | 2021 Abu Dhabi Grand Prix › 개요 |
| 5 | 2021 Abu Dhabi Grand Prix › Mercedes' protest | Formula One regulations › Penalties |
| 6 | 2021 Abu Dhabi Grand Prix › Mercedes' protest | 2021 Abu Dhabi Grand Prix › Legacy |
| 7 | 2021 Abu Dhabi Grand Prix › Pit stops and virtual safety car | Formula One regulations › Tyres |
| 8 | 2021 FIA F1 스포팅 규정 제48조 SAFETY CAR (48.12) | 2021 FIA F1 스포팅 규정 제48조 SAFETY CAR (48.12) |
| 9 | 2021 FIA F1 스포팅 규정 제48조 SAFETY CAR (48.15) | 2021 FIA F1 스포팅 규정 제48조 SAFETY CAR (48.15) |
| 10 | 2021 FIA F1 스포팅 규정 제48조 SAFETY CAR (48.13–48.14) | 2021 FIA F1 스포팅 규정 제48조 SAFETY CAR (48.13–48.14) |

MMR에만 있는 근거 4개.

### Top-k 바꾸기 (설정의 검색 방식)

| Top-k | 근거 수(메타 포함) | 서로 다른 문서 수 | 평균 유사도 |
| --- | --- | --- | --- |
| 3 | 6 | 3 | 0.543 |
| 6 | 9 | 3 | 0.501 |
| 10 | 13 | 4 | 0.536 |

## 2023 R02 사우디아라비아 그랑프리: 알론소가 받은 페널티와 재심 결과는?

### 유사도 검색 vs MMR (Top-k 6)

| 방식 | 서로 다른 문서 수 | 근거 간 평균 유사도 | 출처 종류 |
| --- | --- | --- | --- |
| similarity | 4 | 0.579 | 위키백과 6, FIA 스포팅 규정 3 |
| mmr | 4 | 0.569 | 위키백과 6, FIA 스포팅 규정 3 |

| 순위 | 유사도 검색 | MMR |
| --- | --- | --- |
| 1 | 2023 사우디아라비아 그랑프리 | 2023 사우디아라비아 그랑프리 |
| 2 | 2023 Saudi Arabian Grand Prix › Post-race | 2023 Saudi Arabian Grand Prix › Post-race |
| 3 | 2023 Saudi Arabian Grand Prix › Post-race | 2023 Saudi Arabian Grand Prix › Qualifying classification |
| 4 | 2023 Saudi Arabian Grand Prix › Qualifying classification | 2023 Formula One World Championship › Closing rounds |
| 5 | Sergio Pérez › 2012: Maiden podiums | Sergio Pérez › 2022: Maiden pole and third in the championship |
| 6 | Sergio Pérez › 2014 | Sergio Pérez › 2018 |
| 7 | 2023 Formula One World Championship › Closing rounds | 2023 Formula One World Championship › Mid-season rounds |
| 8 | 2023 FIA F1 스포팅 규정 제54조 INCIDENTS DURING THE SPRINT SESSION OR THE RACE (54.3) | 2023 FIA F1 스포팅 규정 제54조 INCIDENTS DURING THE SPRINT SESSION OR THE RACE (54.3) |
| 9 | 2023 FIA F1 스포팅 규정 제54조 INCIDENTS DURING THE SPRINT SESSION OR THE RACE (54.3) | 2023 FIA F1 스포팅 규정 제54조 INCIDENTS DURING THE SPRINT SESSION OR THE RACE (54.3) |
| 10 | 2023 FIA F1 스포팅 규정 제54조 INCIDENTS DURING THE SPRINT SESSION OR THE RACE (54.4) | 2023 FIA F1 스포팅 규정 제54조 INCIDENTS DURING THE SPRINT SESSION OR THE RACE (54.4) |

MMR에만 있는 근거 3개.

### Top-k 바꾸기 (설정의 검색 방식)

| Top-k | 근거 수(메타 포함) | 서로 다른 문서 수 | 평균 유사도 |
| --- | --- | --- | --- |
| 3 | 6 | 3 | 0.559 |
| 6 | 9 | 4 | 0.565 |
| 10 | 13 | 4 | 0.583 |
