import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import pandas as pd
import time

# --- [1] 구글 시트 연결 설정 ---
@st.cache_resource
def init_connection():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    
    # Secrets에서 정보 가져오기 & 줄바꿈 문자 처리
    key_dict = dict(st.secrets["gcp_service_account"])
    key_dict["private_key"] = key_dict["private_key"].replace("\\n", "\n")
    
    creds = ServiceAccountCredentials.from_json_keyfile_dict(key_dict, scope)
    client = gspread.authorize(creds)
    return client

client = init_connection()

# 본인의 구글 시트 주소 (URL)
url = "https://docs.google.com/spreadsheets/d/1Q4YJBhdUEHwYdMFMSFqbhyNG73z6l2rCObsKALol7IM/edit?gid=0#gid=0" 

try:
    sh = client.open_by_url(url)
except Exception as e:
    st.error(f"시트 연결 실패! URL 확인 필요. {e}")
    st.stop()

ws_users = sh.worksheet("Users")
ws_matches = sh.worksheet("Matches")
ws_bets = sh.worksheet("Bets")

# --- [2] 함수 정의 (정산 기능 추가됨!) ---

def get_user_data(nickname):
    users = ws_users.get_all_records()
    for user in users:
        if str(user['nickname']) == str(nickname):
            return user
    new_user = {'nickname': nickname, 'balance': 10000}
    ws_users.append_row([nickname, 10000])
    return new_user

def update_balance(nickname, amount):
    # gspread의 find 기능을 사용하여 셀 위치 찾기
    cell = ws_users.find(nickname)
    current_balance = int(ws_users.cell(cell.row, 2).value)
    new_balance = current_balance + amount
    ws_users.update_cell(cell.row, 2, new_balance)
    return new_balance

def place_bet(nickname, match_id, choice, amount):
    update_balance(nickname, -amount)
    ws_bets.append_row([
        nickname, match_id, choice, amount, str(datetime.now())
    ])

# 🔥 [핵심] 정산 자동화 함수
def run_admin_settlement():
    st.info("정산을 시작합니다... 잠시만 기다려주세요.")
    
    matches = pd.DataFrame(ws_matches.get_all_records())
    bets = pd.DataFrame(ws_bets.get_all_records())
    
    # I열(is_settled)이 없으면 에러가 날 수 있으니 체크
    if 'is_settled' not in matches.columns:
        st.error("Matches 시트에 'is_settled' 헤더(I1 셀)를 만들어주세요!")
        return

    # 정산 대상: 상태가 FINISHED이고, 아직 정산(TRUE) 안 된 경기
    # (문자열 비교이므로 'TRUE' 텍스트와 비교)
    targets = matches[
        (matches['status'] == 'FINISHED') & 
        (matches['is_settled'] != 'TRUE')
    ]

    if targets.empty:
        st.warning("현재 정산할 경기가 없습니다. (모두 완료되었거나 종료된 경기가 없음)")
        return

    success_count = 0
    
    for idx, match in targets.iterrows():
        match_id = match['match_id']
        result = match['result'] # HOME, DRAW, AWAY
        
        # 배당률 결정
        odds = 1.0
        if result == 'HOME': odds = float(match['home_odds'])
        elif result == 'DRAW': odds = float(match['draw_odds'])
        elif result == 'AWAY': odds = float(match['away_odds'])
        else:
            st.error(f"[{match_id}] 결과값 오류 ({result}). 건너뜁니다.")
            continue

        st.write(f"🔄 **{match['home']} vs {match['away']}** 정산 중... (결과: {result})")
        
        # 해당 경기에 건 내역 필터링
        match_bets = bets[bets['match_id'] == match_id]
        
        for b_idx, bet in match_bets.iterrows():
            if str(bet['choice']) == str(result):
                nickname = bet['nickname']
                amount = int(bet['amount'])
                win_amount = int(amount * odds)
                
                try:
                    update_balance(nickname, win_amount)
                    st.success(f"  -> {nickname} 님에게 {win_amount:,}P 지급 완료")
                except Exception as e:
                    st.error(f"  -> {nickname} 지급 실패: {e}")
        
        # 정산 완료 처리 (엑셀에 TRUE 표시)
        # gspread에서 해당 match_id 셀 찾기
        m_cell = ws_matches.find(match_id)
        # I열(9번째)에 TRUE 입력
        ws_matches.update_cell(m_cell.row, 9, 'TRUE')
        success_count += 1
        
    st.balloons()
    st.success(f"총 {success_count}개 경기 정산 완료!")

def show_ranking():
    st.subheader("🏆 명예의 전당 (Top 10)")
    
    # [1] 데이터 프레임 변환
    data = ws_users.get_all_records()
    df = pd.DataFrame(data)

    # [2] 데이터 정렬 (내림차순)
    # balance 컬럼을 기준으로 높은 숫자부터 정렬
    df_sorted = df.sort_values(by='balance', ascending=False)

    # [3] 등수 매기기 (인덱스 재설정)
    # 정렬 후 섞인 번호를 0, 1, 2... 순서로 다시 매김
    df_sorted = df_sorted.reset_index(drop=True)
    df_sorted.index = df_sorted.index + 1  # 0등은 없으므로 1을 더해서 1등부터 시작

    # [4] 화면 출력
    # 전체 데이터 중 'nickname'과 'balance' 컬럼만 뽑아서 상위 10개 표시
    st.dataframe(
        df_sorted[['nickname', 'balance']].head(10), 
        use_container_width=True
    )


# --- [3] UI 디자인 ---
st.set_page_config(page_title="캠퍼스 토토", page_icon="⚽")

# 사이드바 (로그인 & 관리자)
with st.sidebar:
    st.title("⚽ 메뉴")
    
    # 탭을 나눠서 일반 유저용 / 관리자용 구분
    tab1, tab2 = st.tabs(["로그인", "관리자"])
    
    # 1. 일반 로그인 탭
    with tab1:
        nickname = st.text_input("닉네임 입력", key="login_id")
        user_info = None
        if nickname:
            user_info = get_user_data(nickname)
            st.success(f"{nickname}님 접속 중")
            st.metric("보유 포인트", f"{user_info['balance']:,} P")
            if st.button("내 잔액 새로고침"):
                st.rerun()


    # 2. 관리자 탭 (비밀번호 걸기)
# --- [기존 import 문 아래에 수학 라이브러리 추가] ---
import math

# --- [1] 배당률 자동 계산 함수 (ELO 공식 적용) ---
def calculate_auto_odds(home_elo, away_elo):
    diff = home_elo - away_elo
    # 승리 확률 계산 (로지스틱 곡선)
    prob_home = 1 / (1 + 10 ** (-diff / 400))
    prob_draw = 0.30 * (1 - abs(prob_home - 0.5) * 2) # 무승부 확률 보정
    
    real_prob_home = prob_home * (1 - prob_draw)
    real_prob_away = (1 - prob_home) * (1 - prob_draw)
    
    # 1.05배 미만은 재미없으니 최소 배당 보정
    odds_home = max(1.05, round(1 / real_prob_home, 2))
    odds_draw = max(1.05, round(1 / prob_draw, 2))
    odds_away = max(1.05, round(1 / real_prob_away, 2))
    
    return odds_home, odds_draw, odds_away

# ... (기존 시트 연결 코드는 그대로 유지) ...



# --- [3] UI 디자인 및 사이드바 ---
st.set_page_config(page_title="DDC CAMP-US CUP TOTO", page_icon="⚽")

# [중요] Teams 시트 연결 시도 (없으면 에러 방지)
try:
    ws_teams = sh.worksheet("Teams")
except:
    ws_teams = None

# ▼▼▼ 여기서부터 사이드바 코드 시작 ▼▼▼
with st.sidebar:
    st.title("⚽ 메뉴")
    
    # 탭 생성 (이 줄이 없으면 탭도, 입력창도 안 나옵니다!)
    tab1, tab2 = st.tabs(["로그인", "관리자"])
    
    # -------------------------------------------------------
    # 1. 일반 로그인 탭
    # -------------------------------------------------------
    with tab1:
        nickname = st.text_input("닉네임 입력", key="login_id")
        user_info = None
        if nickname:
            user_info = get_user_data(nickname)
            st.success(f"{nickname}님 접속 중")
            st.metric("보유 포인트", f"{user_info['balance']:,} P")
            if st.button("내 잔액 새로고침"):
                st.rerun()

    # -------------------------------------------------------
    # 2. 관리자 탭 (수정된 부분)
    # -------------------------------------------------------
    with tab2:
        # [체크포인트] 이 줄이 있어야 입력창이 뜹니다!
        admin_pw = st.text_input("관리자 암호", type="password")
        
        if admin_pw == "admin1234":
            st.success("🔓 관리자 모드 접속 완료")
            
            # --- [기능 A] 경기 등록하기 (배당률 자동) ---
            st.subheader("📝 새 경기 등록")
            
            if ws_teams is None:
                st.error("⚠️ 구글 시트에 'Teams' 탭이 없습니다! 먼저 만들어주세요.")
            else:
                # Teams 데이터 가져오기
                try:
                    teams_data = ws_teams.get_all_records()
                    df_teams = pd.DataFrame(teams_data)
                    team_list = df_teams['team_name'].tolist()
                    
                    if not team_list:
                        st.warning("Teams 시트에 팀 데이터가 비어있습니다.")
                    else:
                        col1, col2 = st.columns(2)
                        home_team = col1.selectbox("홈 팀", team_list, key="sel_home")
                        away_team = col2.selectbox("원정 팀", team_list, index=min(1, len(team_list)-1), key="sel_away")
                        
                        # ELO 점수 찾기
                        home_elo = df_teams[df_teams['team_name'] == home_team]['elo'].values[0]
                        away_elo = df_teams[df_teams['team_name'] == away_team]['elo'].values[0]
                        
                        # 배당률 계산
                        o_h, o_d, o_a = calculate_auto_odds(home_elo, away_elo)
                        
                        st.info(f"📊 {home_team}({home_elo}) vs {away_team}({away_elo})")
                        c1, c2, c3 = st.columns(3)
                        c1.metric("홈승", o_h)
                        c2.metric("무승부", o_d)
                        c3.metric("원정승", o_a)
                        
                        if st.button("경기 등록하기", key="btn_reg"):
                            new_id = f"M{int(time.time())}"
                            ws_matches.append_row([
                                new_id, home_team, away_team, 
                                o_h, o_d, o_a, 
                                "WAITING", "" 
                            ])
                            st.success("등록 완료!")
                            time.sleep(1)
                            st.rerun()

                except Exception as e:
                    st.error(f"데이터 로딩 오류: {e}")

            st.markdown("---")
            
            # --- [기능 B] 정산하기 ---
            st.subheader("💰 경기 결과 정산")
            if st.button("종료된 경기 일괄 정산하기"):
                run_admin_settlement()
                
        elif admin_pw:
            st.warning("암호가 틀렸습니다.")
# ▲▲▲ 여기까지 사이드바 코드 끝 ▲▲▲


# --- 메인 로직 (경기 목록 등) ---
# (기존 코드와 동일)
matches = ws_matches.get_all_records()
df_matches = pd.DataFrame(matches)

if not df_matches.empty and 'status' in df_matches.columns:
    active_matches = df_matches[df_matches['status'] == 'WAITING']
else:
    active_matches = pd.DataFrame()

if active_matches.empty:
    st.info("현재 베팅 가능한 경기가 없습니다.")
else:
    st.markdown("### 📅 진행 중인 경기")
    for idx, match in active_matches.iterrows():
        with st.container():
            st.markdown(f"**[{match['match_id']}] {match['home']} vs {match['away']}**")
            col1, col2, col3 = st.columns(3)
            col1.metric(f"홈승 ({match['home']})", match['home_odds'])
            col2.metric("무승부", match['draw_odds'])
            col3.metric(f"원정승 ({match['away']})", match['away_odds'])
            
            with st.expander("베팅하기"):
                choice = st.radio("선택", ['HOME', 'DRAW', 'AWAY'], key=f"c_{match['match_id']}", horizontal=True)
                amount = st.number_input("금액", 100, user_info['balance'], 100, key=f"a_{match['match_id']}")
                
                if st.button("베팅 확정", key=f"b_{match['match_id']}"):
                    if amount > user_info['balance']:
                        st.error("잔액 부족!")
                    else:
                        with st.spinner("처리 중..."):
                            place_bet(nickname, match['match_id'], choice, amount)
                        st.success("베팅 완료!")
                        st.rerun()
            st.markdown("---")

# 내 베팅 기록
st.subheader("📜 나의 베팅 기록")
all_bets = ws_bets.get_all_records()
my_bets = [bet for bet in all_bets if str(bet['nickname']) == str(nickname)]
if my_bets:
    st.table(pd.DataFrame(my_bets)[['match_id', 'choice', 'amount', 'timestamp']])
