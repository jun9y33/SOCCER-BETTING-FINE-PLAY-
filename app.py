import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import pandas as pd
import time
import math

# --- [0] 기본 설정 (반드시 맨 처음에 와야 함!) ---
st.set_page_config(page_title="DDC CAMP-US CUP TOTO", page_icon="⚽", layout="wide")

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
    ws_users = sh.worksheet("Users")
    ws_matches = sh.worksheet("Matches")
    ws_bets = sh.worksheet("Bets")
    # Teams 시트는 없을 수도 있으니 예외처리
    try:
        ws_teams = sh.worksheet("Teams")
    except:
        ws_teams = None
except Exception as e:
    st.error(f"시트 연결 실패! 공유 설정과 시트 이름(Users, Matches, Bets)을 확인하세요.\n에러 내용: {e}")
    st.stop()

# --- [2] 핵심 로직 함수들 ---

def get_user_data(nickname):
    """유저 정보 조회 및 신규 생성"""
    users = ws_users.get_all_records()
    for user in users:
        if str(user['nickname']) == str(nickname):
            return user
    new_user = {'nickname': nickname, 'balance': 10000}
    ws_users.append_row([nickname, 10000])
    return new_user

def update_balance(nickname, amount):
    """잔액 변경 (베팅 차감 or 당첨금 지급)"""
    cell = ws_users.find(nickname)
    current_balance = int(ws_users.cell(cell.row, 2).value)
    new_balance = current_balance + amount
    ws_users.update_cell(cell.row, 2, new_balance)
    return new_balance

def place_bet(nickname, match_id, choice, amount):
    """베팅 실행"""
    update_balance(nickname, -amount)
    ws_bets.append_row([
        nickname, match_id, choice, amount, str(datetime.now())
    ])

def calculate_auto_odds(home_elo, away_elo):
    """ELO 점수 기반 배당률 자동 계산"""
    diff = home_elo - away_elo
    prob_home = 1 / (1 + 10 ** (-diff / 400))
    prob_draw = 0.30 * (1 - abs(prob_home - 0.5) * 2)
    
    real_prob_home = prob_home * (1 - prob_draw)
    real_prob_away = (1 - prob_home) * (1 - prob_draw)
    
    odds_home = max(1.05, round(1 / real_prob_home, 2))
    odds_draw = max(1.05, round(1 / prob_draw, 2))
    odds_away = max(1.05, round(1 / real_prob_away, 2))
    return odds_home, odds_draw, odds_away

def run_admin_settlement():
    """관리자용: 종료된 경기 정산"""
    st.info("정산을 시작합니다... 잠시만 기다려주세요.")
    matches = pd.DataFrame(ws_matches.get_all_records())
    bets = pd.DataFrame(ws_bets.get_all_records())
    
    if 'is_settled' not in matches.columns:
        st.error("Matches 시트에 'is_settled' 헤더(I1 셀)를 만들어주세요!")
        return

    targets = matches[(matches['status'] == 'FINISHED') & (matches['is_settled'] != 'TRUE')]

    if targets.empty:
        st.warning("정산할 경기가 없습니다.")
        return

    success_count = 0
    for idx, match in targets.iterrows():
        match_id = match['match_id']
        result = match['result']
        
        # 배당률 가져오기
        odds = 1.0
        if result == 'HOME': odds = float(match['home_odds'])
        elif result == 'DRAW': odds = float(match['draw_odds'])
        elif result == 'AWAY': odds = float(match['away_odds'])
        else:
            continue # 결과 입력 오류시 패스

        st.write(f"🔄 **{match['home']} vs {match['away']}** 정산 중... (결과: {result})")
        
        # 당첨자 찾기
        match_bets = bets[bets['match_id'] == match_id]
        for b_idx, bet in match_bets.iterrows():
            if str(bet['choice']) == str(result):
                win_amount = int(bet['amount'] * odds)
                try:
                    update_balance(bet['nickname'], win_amount)
                    st.success(f"  -> {bet['nickname']} : +{win_amount:,}P")
                except:
                    st.error(f"  -> {bet['nickname']} 지급 실패")
        
        # 정산 완료 마킹 (I열 = 9번째)
        m_cell = ws_matches.find(match_id)
        ws_matches.update_cell(m_cell.row, 9, 'TRUE')
        success_count += 1
        
    st.balloons()
    st.success(f"총 {success_count}개 경기 정산 완료!")

def show_ranking():
    """랭킹 보드 출력"""
    data = ws_users.get_all_records()
    df = pd.DataFrame(data)
    if not df.empty:
        df_sorted = df.sort_values(by='balance', ascending=False).reset_index(drop=True)
        df_sorted.index = df_sorted.index + 1
        st.dataframe(df_sorted[['nickname', 'balance']].head(10), use_container_width=True)
    else:
        st.text("아직 유저 데이터가 없습니다.")

# --- [3] UI 디자인 (사이드바) ---
nickname = None # 초기화
user_info = None

with st.sidebar:
    st.title("⚽ 메뉴")
    tab1, tab2 = st.tabs(["로그인", "관리자"])
    
    # [탭 1] 로그인
    with tab1:
        # [수정] 혹시 모를 에러 방지를 위해 key값 뒤에 _sidebar 붙임
        nickname_input = st.text_input("닉네임 입력", key="login_id_sidebar")
        if nickname_input:
            nickname = nickname_input # 전역 변수에 저장
            user_info = get_user_data(nickname)
            st.success(f"✅ {nickname}님 환영합니다!")
            st.metric("내 포인트", f"{user_info['balance']:,} P")
            if st.button("새로고침"):
                st.rerun()

    # [탭 2] 관리자
    with tab2:
        admin_pw = st.text_input("관리자 암호", type="password", key="admin_pw_input")
        if admin_pw == "admin1234":
            st.success("🔓 관리자 모드")
            
            st.markdown("### 📝 경기 등록")
            if ws_teams:
                try:
                    teams_df = pd.DataFrame(ws_teams.get_all_records())
                    team_list = teams_df['team_name'].tolist()
                    if team_list:
                        c1, c2 = st.columns(2)
                        h_team = c1.selectbox("홈", team_list, key='h_sel')
                        a_team = c2.selectbox("원정", team_list, index=min(1, len(team_list)-1), key='a_sel')
                        
                        h_elo = teams_df[teams_df['team_name']==h_team]['elo'].values[0]
                        a_elo = teams_df[teams_df['team_name']==a_team]['elo'].values[0]
                        oh, od, oa = calculate_auto_odds(h_elo, a_elo)
                        
                        st.info(f"예상 배당: 승 {oh} / 무 {od} / 패 {oa}")
                        if st.button("경기 등록", key="reg_btn"):
                            new_id = f"M{int(time.time())}"
                            ws_matches.append_row([new_id, h_team, a_team, oh, od, oa, "WAITING", "", "FALSE"])
                            st.success("등록 완료")
                            time.sleep(1)
                            st.rerun()
                    else:
                        st.warning("팀 데이터가 없습니다.")
                except Exception as e:
                    st.error(f"팀 데이터 로딩 실패: {e}")
            else:
                st.warning("Teams 시트가 없습니다.")
            
            st.markdown("---")
            if st.button("💰 정산 실행", key="settle_btn"):
                run_admin_settlement()

# --- [4] 메인 화면 ---
st.title("🏆 DDC CAMP-US CUP")

if not nickname:
    st.warning("👈 왼쪽 사이드바에서 닉네임을 먼저 입력해주세요!")
    st.stop() # 닉네임 없으면 여기서 멈춤

# 메인 탭 구성 (베팅 vs 랭킹)
main_tab1, main_tab2 = st.tabs(["🔥 베팅하기", "📊 랭킹 보드"])

with main_tab1:
    matches = ws_matches.get_all_records()
    df_matches = pd.DataFrame(matches)

    if not df_matches.empty and 'status' in df_matches.columns:
        active_matches = df_matches[df_matches['status'] == 'WAITING']
    else:
        active_matches = pd.DataFrame()

    if active_matches.empty:
        st.info("현재 오픈된 경기가 없습니다.")
    else:
        for idx, match in active_matches.iterrows():
            with st.container(border=True): # 깔끔한 박스 디자인
                st.subheader(f"{match['home']} vs {match['away']}")
                c1, c2, c3 = st.columns(3)
                c1.metric("홈 승", match['home_odds'])
                c2.metric("무승부", match['draw_odds'])
                c3.metric("원정 승", match['away_odds'])
                
                sel = st.radio("선택", ["HOME", "DRAW", "AWAY"], key=f"s_{match['match_id']}", horizontal=True)
                amt = st.number_input("베팅액", 100, user_info['balance'], 100, key=f"m_{match['match_id']}")
                
                if st.button("베팅하기", key=f"b_{match['match_id']}"):
                    if amt > user_info['balance']:
                        st.error("잔액 부족!")
                    else:
                        place_bet(nickname, match['match_id'], sel, amt)
                        st.success("베팅 성공!")
                        st.rerun()

    st.markdown("---")
    st.subheader("📜 내 베팅 내역")
    all_bets = ws_bets.get_all_records()
    my_bets = [b for b in all_bets if str(b['nickname']) == str(nickname)]
    if my_bets:
        st.table(pd.DataFrame(my_bets)[['match_id', 'choice', 'amount', 'timestamp']])

with main_tab2:
    show_ranking()
