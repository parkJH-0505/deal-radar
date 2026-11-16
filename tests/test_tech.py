from radar import tech as T
from radar.pipeline import load_cfg


def test_tech_industry_classification():
    cfg = load_cfg()
    yes = ["응용 소프트웨어 개발 및 공급업", "기타 반도체 소자 제조업", "산업용 로봇 제조업",
           "의학 및 약학 연구개발업", "데이터베이스 및 온라인 정보 제공업", "그 외 기타 의료용 기기 제조업"]
    no = ["컴퓨터 및 주변장치  소프트웨어 도매업", "의약품 도매업", "전자 게임장 운영업", "컴퓨터 학원",
          "화장품 및 화장용품 도매업", "식품제조가공업", "기타 인문 및 사회과학 연구개발업"]
    assert all(T.is_tech(n, cfg) for n in yes)
    assert not any(T.is_tech(n, cfg) for n in no)


def test_noise_service():
    cfg = load_cfg()
    assert T.is_noise_service("변호사업", cfg) and T.is_noise_service("개인 간병 및 유사 서비스업", cfg)
    assert not T.is_noise_service("응용 소프트웨어 개발 및 공급업", cfg)
