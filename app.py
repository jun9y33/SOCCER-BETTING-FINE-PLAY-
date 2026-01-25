import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import pandas as pd
import time
import math

# --- [0] 기본 설정 ---
st.set_page_config(page_title="DDC CAMP-US CUP BETTING", page_icon="⚽", layout="wide")

# --- [1] 구글 시트 연결 설정 ---
@st.cache_resource
def init_connection():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    key_dict = dict(st.secrets["gcp_service_account"])
    key_dict["private_key"] = key_dict["private_key"].replace("\\n", "\n")
    creds = ServiceAccountCredentials.from_json_keyfile_dict(key_dict, scope)
    client = gspread.authorize(creds)
    return client

client = init_connection()

# 본인의 구글 시트 주소
url = "https://docs.google.com/spreadsheets/d/1Q4YJBhdUEHwYdMFMSFqbhyNG73z6l2rCObsKALol7IM/edit?gid=0#gid=0" 

try:
    sh = client.open_by_url(url)
    ws_users = sh.worksheet("Users")
    ws_matches = sh.worksheet("Matches")
    ws_bets = sh.worksheet("Bets")
    try:
        ws_teams = sh.worksheet("Teams")
    except:
        ws_teams = None
except Exception as e:
    st.error(f"시트 연결 실패! {e}")
    st.stop()

# --- [2] 핵심 로직 함수들 ---

def check_user_exists(nickname):
    try:
        existing_nicknames = ws_users.col_values(1)
        return str(nickname) in [str(n) for n in existing_nicknames]
    except:
        return False

def create_new_user(nickname):
    """신규 유저 생성"""
    # [수정] 초기 자금을 3000으로 변경
    initial_balance = 3000 
    
    ws_users.append_row([nickname, initial_balance])
    return {'nickname': nickname, 'balance': initial_balance}

def get_user_info(nickname):
    try:
        cell = ws_users.find(nickname)
        balance = ws_users.cell(cell.row, 2).value
        return {'nickname': nickname, 'balance': int(balance)}
    except:
        return None
    
def update_balance(nickname, amount):
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

def calculate_auto_odds(home_elo, away_elo):
    diff = home_elo - away_elo
    prob_home = 1 / (1 + 10 ** (-diff / 400))
    prob_draw = 0.30 * (1 - abs(prob_home - 0.5) * 2)
    real_prob_home = prob_home * (1 - prob_draw)
    real_prob_away = (1 - prob_home) * (1 - prob_draw)
    odds_home = max(1.05, round(1 / real_prob_home, 2))
    odds_draw = max(1.05, round(1 / prob_draw, 2))
    odds_away = max(1.05, round(1 / real_prob_away, 2))
    return odds_home, odds_draw, odds_away

def update_team_elo_advanced(home_team, away_team, result, h_xg, a_xg, h_pass, a_pass, h_ppda, a_ppda):
    K = 32
    try:
        cell_h = ws_teams.find(home_team)
        cell_a = ws_teams.find(away_team)
        elo_h = int(ws_teams.cell(cell_h.row, 2).value)
        elo_a = int(ws_teams.cell(cell_a.row, 2).value)
    except:
        st.error("팀 정보를 찾을 수 없습니다.")
        return

    diff = elo_h - elo_a
    expected_h = 1 / (1 + 10 ** (-diff / 400))
    expected_a = 1 - expected_h
    
    if result == 'HOME': actual_h, actual_a = 1, 0
    elif result == 'DRAW': actual_h, actual_a = 0.5, 0.5
    else: actual_h, actual_a = 0, 1
    
    base_change_h = K * (actual_h - expected_h)
    
    W_XG = 10.0
    W_PPDA = 1.0
    W_PASS = 0.1
    
    diff_xg = h_xg - a_xg
    diff_pass = h_pass - a_pass
    diff_ppda = a_ppda - h_ppda 
    
    performance_bonus = (diff_xg * W_XG) + (diff_pass * W_PASS) + (diff_ppda * W_PPDA)
    
    total_change = base_change_h + performance_bonus
    new_elo_h = round(elo_h + total_change)
    new_elo_a = round(elo_a - total_change)
    
    ws_teams.update_cell(cell_h.row, 2, new_elo_h)
    ws_teams.update_cell(cell_a.row, 2, new_elo_a)
    st.toast(f"📊 전술 반영 완료! {home_team}: {new_elo_h}({int(total_change):+})")

def run_admin_settlement():
    st.info("정산을 시작합니다...")
    matches = pd.DataFrame(ws_matches.get_all_records())
    bets = pd.DataFrame(ws_bets.get_all_records())
    
    if 'is_settled' not in matches.columns:
        st.error("Matches 시트에 'is_settled' 헤더가 없습니다!")
        return

    # 안전하게 문자열 변환 후 비교
    matches['is_settled'] = matches['is_settled'].astype(str)
    targets = matches[(matches['status'] == 'FINISHED') & (matches['is_settled'] != 'TRUE')]

    if targets.empty:
        st.warning("정산할 경기가 없습니다.")
        return

    success_count = 0
    for idx, match in targets.iterrows():
        match_id = match['match_id']
        result = match['result']
        
        # 데이터 안전하게 가져오기 (빈칸 처리)
        def get_val(row, col):
            val = row.get(col, 0)
            return float(val) if val != '' else 0.0

        h_xg = get_val(match, 'h_xg')
        a_xg = get_val(match, 'a_xg')
        h_pass = get_val(match, 'h_pass')
        a_pass = get_val(match, 'a_pass')
        h_ppda = get_val(match, 'h_ppda')
        a_ppda = get_val(match, 'a_ppda')
        
        # 배당률
        odds = 1.0
        if result == 'HOME': odds = float(match['home_odds'])
        elif result == 'DRAW': odds = float(match['draw_odds'])
        elif result == 'AWAY': odds = float(match['away_odds'])
        else: continue

        st.write(f"🔄 **{match['home']} vs {match['away']}** 정산 중... (결과: {result})")

        if ws_teams:
            update_team_elo_advanced(
                match['home'], match['away'], result,
                h_xg, a_xg, h_pass, a_pass, h_ppda, a_ppda
            )
            
        match_bets = bets[bets['match_id'] == match_id]
        for b_idx, bet in match_bets.iterrows():
            if str(bet['choice']) == str(result):
                win_amount = int(bet['amount'] * odds)
                try:
                    update_balance(bet['nickname'], win_amount)
                    st.success(f"  -> {bet['nickname']} : +{win_amount:,}P")
                except:
                    st.error(f"  -> {bet['nickname']} 지급 실패")
        
        # 정산 완료 마킹 (15번째 열 = O열)
        # Matches 헤더가 바뀌면 이 숫자도 바뀌어야 함! (현재 기준 15)
        m_cell = ws_matches.find(match_id)
        ws_matches.update_cell(m_cell.row, 15, 'TRUE')
        success_count += 1
        
    st.balloons()
    st.success(f"총 {success_count}개 경기 정산 완료!")

def show_ranking():
    data = ws_users.get_all_records()
    df = pd.DataFrame(data)
    if not df.empty:
        df_sorted = df.sort_values(by='balance', ascending=False).reset_index(drop=True)
        df_sorted.index = df_sorted.index + 1
        st.dataframe(df_sorted[['nickname', 'balance']].head(10), use_container_width=True)
    else:
        st.text("아직 유저 데이터가 없습니다.")

# --- [3] UI 디자인 ---

# [수정 1] 세션 상태 초기화 (로그인 유지의 핵심)
if 'nickname' not in st.session_state:
    st.session_state['nickname'] = None
if 'user_info' not in st.session_state:
    st.session_state['user_info'] = None

with st.sidebar:
    st.title("⚽ 메뉴")
    tab1, tab2 = st.tabs(["로그인", "관리자"])
    
    with tab1:
        auth_mode = st.radio("모드 선택", ["로그인", "회원가입"], horizontal=True)
        nickname_input = st.text_input("닉네임 입력", key="login_id_sidebar")
        
        if st.button("확인"):
            if not nickname_input:
                st.warning("닉네임을 입력해주세요.")
            else:
                is_exist = check_user_exists(nickname_input)
                
                if auth_mode == "로그인":
                    if is_exist:
                        st.session_state['nickname'] = nickname_input
                        st.session_state['user_info'] = get_user_info(nickname_input)
                        st.success(f"✅ 접속 성공!")
                        st.rerun()
                    else:
                        st.error("❌ 존재하지 않는 닉네임입니다.")
                elif auth_mode == "회원가입":
                    if is_exist:
                        st.error("⚠️ 이미 존재하는 이름입니다!")
                    else:
                        new_user = create_new_user(nickname_input)
                        st.session_state['nickname'] = nickname_input
                        st.session_state['user_info'] = new_user
                        st.success(f"🎉 가입 완료!")
                        st.balloons()
                        time.sleep(1)
                        st.rerun()

        # 로그인 상태라면 정보 표시
        if st.session_state['nickname']:
            st.markdown("---")
            st.info(f"👤 **{st.session_state['nickname']}**님")
            
            if st.button("내 포인트 확인"):
                info = get_user_info(st.session_state['nickname'])
                st.session_state['user_info'] = info
                st.metric("현재 잔액", f"{info['balance']:,} P")
            
            if st.button("로그아웃"):
                st.session_state['nickname'] = None
                st.session_state['user_info'] = None
                st.rerun()
                
    with tab2:
        admin_pw = st.text_input("관리자 암호", type="password", key="admin_pw_input")
        if admin_pw == "fineplay1234": # 비번 유지
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
                        
                        st.info(f"예상 배당: {oh} / {od} / {oa}")
                        if st.button("경기 등록", key="reg_btn"):
                            new_id = f"M{int(time.time())}"
                            # [수정 2] 빈칸 8개를 넣어서 열 개수를 맞춤! (Result~PPDA까지)
                            # 순서: ID, Home, Away, Odds*3, Status, Result, xG*2, Pass*2, PPDA*2, Settled
                            ws_matches.append_row([
                                new_id, h_team, a_team, oh, od, oa, 
                                "WAITING", "", "", "", "", "", "", "", "FALSE"
                            ])
                            st.success("등록 완료")
                            time.sleep(1)
                            st.rerun()
                    else:
                        st.warning("팀 데이터 없음")
                except Exception as e:
                    st.error(f"오류: {e}")
            else:
                st.warning("Teams 시트 없음")
            
            st.markdown("---")
            if st.button("💰 정산 실행", key="settle_btn"):
                run_admin_settlement()

# --- [4] 메인 화면 ---
st.title("🏆 DDC CAMP-US CUP")

# 세션에 정보가 없으면 차단
if not st.session_state['nickname']:
    st.warning("👈 왼쪽 사이드바에서 로그인해주세요!")
    st.stop()

main_tab1, main_tab2 = st.tabs(["🔥 베팅하기", "📊 랭킹 보드"])

# --- [수정된 베팅 탭 코드] ---
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
        # 베팅 한도 설정 (상수로 관리하면 나중에 바꾸기 편함)
        MIN_BET = 500
        MAX_BET = 1000

        for idx, match in active_matches.iterrows():
            with st.container(border=True):
                st.subheader(f"{match['home']} vs {match['away']}")
                c1, c2, c3 = st.columns(3)
                c1.metric("홈 승", match['home_odds'])
                c2.metric("무승부", match['draw_odds'])
                c3.metric("원정 승", match['away_odds'])
                
                # [중요] 베팅 입력 UI 개선
                current_balance = st.session_state['user_info']['balance']
                
                # 1. 최소 금액(500원)도 없으면 베팅 불가 처리
                if current_balance < MIN_BET:
                    st.error(f"잔액 부족 (최소 {MIN_BET} P 필요)")
                else:
                    # 2. 선택지 (승/무/패)
                    sel = st.radio("선택", ["HOME", "DRAW", "AWAY"], key=f"s_{match['match_id']}", horizontal=True)
                    
                    # 3. 베팅액 입력 (슬라이더나 숫자 입력 사용)
                    # 최대 베팅액은 '경기제한(1000)'과 '내잔액' 중 작은 값으로 설정
                    # 예: 내 돈이 800원 -> 최대 800원 / 내 돈이 5000원 -> 최대 1000원
                    effective_max_bet = min(MAX_BET, current_balance)
                    
                    amt = st.number_input(
                        f"베팅액 ({MIN_BET} ~ {MAX_BET})", 
                        min_value=MIN_BET, 
                        max_value=effective_max_bet, 
                        step=100, 
                        key=f"m_{match['match_id']}"
                    )
                    
                    # 4. 베팅 버튼
                    if st.button("베팅하기", key=f"b_{match['match_id']}"):
                        # 더블 체크 (보안)
                        if amt < MIN_BET or amt > MAX_BET:
                            st.error(f"베팅 금액은 {MIN_BET}~{MAX_BET} 사이여야 합니다.")
                        elif amt > current_balance:
                            st.error("잔액이 부족합니다.")
                        else:
                            place_bet(st.session_state['nickname'], match['match_id'], sel, amt)
                            st.success(f"{amt} P 베팅 성공!")
                            
                            # 잔액 갱신 및 화면 리로드
                            new_info = get_user_info(st.session_state['nickname'])
                            st.session_state['user_info'] = new_info
                            time.sleep(0.5)
                            st.rerun()

    st.markdown("---")
    st.subheader("📜 내 베팅 내역")
    all_bets = ws_bets.get_all_records()
    my_bets = [b for b in all_bets if str(b['nickname']) == str(st.session_state['nickname'])]
    if my_bets:
        # 보기 좋게 최신순 정렬 (timestamp 기준 내림차순) 같은 거 하면 좋음
        df_my_bets = pd.DataFrame(my_bets)[['match_id', 'choice', 'amount', 'timestamp']]
        st.table(df_my_bets)

with main_tab2:
    show_ranking()
