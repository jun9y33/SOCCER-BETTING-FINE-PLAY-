import math

def calculate_auto_odds(home_score, away_score):
    """
    두 팀의 전력 점수(ELO)를 입력받아 승/무/패 배당률을 자동 계산하는 함수
    """
    
    # [1] 전력 차이 계산
    diff = home_score - away_score
    
    # [2] 홈팀의 승리 확률 계산 (ELO 공식 활용)
    # 400점 차이가 날 때 승률이 10배 차이난다는 통계적 공식
    prob_home = 1 / (1 + 10 ** (-diff / 400))
    
    # [3] 무승부 확률 보정 (휴리스틱)
    # 전력 차가 적을수록(승률이 50%에 가까울수록) 무승부 확률을 높게 설정
    # 여기서는 최대 무승부 확률을 30%(0.3)로 잡음
    prob_draw = 0.30 * (1 - abs(prob_home - 0.5) * 2)
    
    # [4] 확률 재분배 (전체 합이 100%가 되도록 조정)
    # 무승부 확률을 떼어내고 남은 확률을 승/패가 나눠가짐
    real_prob_home = prob_home * (1 - prob_draw)
    real_prob_away = (1 - prob_home) * (1 - prob_draw)
    
    # [5] 배당률로 변환 (1 / 확률)
    # 소수점 2자리에서 반올림
    odds_home = round(1 / real_prob_home, 2)
    odds_draw = round(1 / prob_draw, 2)
    odds_away = round(1 / real_prob_away, 2)
    
    return odds_home, odds_draw, odds_away

# --- 실행 예시 ---
team_a_rating = 1600  # 강팀 (경영대)
team_b_rating = 1400  # 약팀 (인문대)

h, d, a = calculate_auto_odds(team_a_rating, team_b_rating)

print(f"홈팀 전력: {team_a_rating} vs 원정팀 전력: {team_b_rating}")
print(f"자동 생성된 배당률 -> 승: {h} | 무: {d} | 패: {a}")
