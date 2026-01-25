import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import pandas as pd
import time
import math

# --- [0] 기본 설정 ---
st.set_page_config(page_title="DDC 승부예측 챌린지", page_icon="⚽", layout="wide")

# --- [1] 구글 시트 연결 설정 ---
@st.cache_resource
def init_connection():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    key_dict = dict(st.secrets["gcp_service_account"])
    key_dict["private_key"] = key_dict["private_key"].replace("\\n", "\n")
    creds = ServiceAccountCredentials.from_json_keyfile_dict(key_dict, scope)
    client = gspread.authorize(creds)
    return client

# 시트 연결 (실패 시 중단)
try:
    client = init_connection()
    url = "https://docs.google.com/spreadsheets/d/1Q4YJBhdUEHwYdMFMSFqbhyNG73z6l2rCObsKALol7IM/edit?gid=0#gid=0" 
    sh = client.open_by_url(url)
    ws_users = sh.worksheet("Users")
    ws_matches = sh.worksheet("Matches")
    ws_bets = sh.worksheet("Bets")
    try:
        ws_teams = sh.worksheet("Teams")
    except:
        ws_teams = None
except Exception as e:
    st.error(f"⚠️ 구글 시트 연결 오류: {e}")
    st.stop()

# --- [2] 헬퍼 함수: API 호출 없이 행 번호 찾기 (핵심!) ---

def get_row_index(dataframe, column_name, value):
    """
    구글 시트에 'find'를 요청하지 않고, 
    이미 받아온 데이터프레임에서 몇 번째 줄인지 계산함.
    (헤더가 1행이므로, 인덱스+2가 실제 시트 행 번호)
    """
    try:
        # 데이터프레임에서 해당 값의 인덱스 찾기
        idx = dataframe[dataframe[column_name].astype(str) == str(value)].index[0]
        return idx + 2 # 0-based index + 1(헤더) + 1(행번호보정)
    except:
        return None

# --- [3] 핵심 로직 ---

def create_new_user(nickname):
    ws_users.append_row([nickname, 3000])
    return {'nickname': nickname, 'balance': 3000}

# 잔액 업데이트 (읽기 없이 바로 쓰기)
def update_balance_optimized(nickname, amount, user_df):
    row_idx = get_row_index(user_df, 'nickname', nickname)
    if row_idx:
        # 현재 잔액 계산 (메모리상에서)
        current_bal = int(user_df.loc[user_df['nickname'] == nickname, 'balance'].values[0])
        new_bal = current_bal + amount
        
        # 구글 시트에 바로 덮어쓰기 (Read X, Write O)
        # B열(2번째 열) 업데이트
        ws_users.update_cell(row_idx, 2, new_bal)
        return new_bal
    return None

# 베팅 실행
def place_bet_optimized(nickname, match_id, choice, amount, user_df):
    # 1. 잔액 차감 (최적화 버전)
    new_bal = update_balance_optimized(nickname, -amount, user_df)
    
    # 2. 베팅 내역 기록 (Write only)
    ws_bets.append_row([
        nickname, match_id, choice, amount, str(datetime.now())
    ])
    return new_bal

def calculate_auto_odds(home_elo, away_elo):
    diff = home_elo - away_elo
    prob_home = 1 / (1 + 10 ** (-diff / 400))
    prob_draw = 0.30 * (1 - abs(prob_home - 0.5) * 2)
    real_prob_home = prob_home * (1 - prob_draw)
    real_prob_away = (1 - prob_home) * (1 - prob_draw)
    
    MAX_ODDS = 5.0
    odds_home = min(MAX_ODDS, max(1.05, round(1 / real_prob_home, 2)))
    odds_draw = min(MAX_ODDS, max(1.05, round(1 / prob_draw, 2)))
    odds_away = min(MAX_ODDS, max(1.05, round(1 / real_prob_away, 2)))
    return odds_home, odds_draw, odds_away

def update_team_elo_advanced(home_team, away_team, result, h_xg, a_xg, h_pass, a_pass, h_ppda, a_ppda):
    # (기존 로직 유지)
    K = 32
    try:
        # 여기서는 어쩔 수 없이 find를 쓰지만, 관리자만 쓰는 기능이라 괜찮음
        cell_h = ws_teams.find(home_team)
        cell_a = ws_teams.find(away_team)
        elo_h = int(ws_teams.cell(cell_h.row, 2).value)
        elo_a = int(ws_teams.cell(cell_a.row, 2).value)
        
        diff = elo_h - elo_a
        expected_h = 1 / (1 + 10 ** (-diff / 400))
        expected_a = 1 - expected_h
        
        if result == 'HOME': actual_h, actual_a = 1, 0
        elif result == 'DRAW': actual_h, actual_a = 0.5, 0.5
        else: actual_h, actual_a = 0, 1
        
        base_change_h = K * (actual_h - expected_h)
        
        W_XG, W_PPDA, W_PASS = 10.0, 1.0, 0.1
        performance_bonus = ((h_xg - a_xg) * W_XG) + ((h_pass - a_pass) * W_PASS) + ((a_ppda - h_ppda) * W_PPDA)
        
        total_change = base_change_h + performance_bonus
        new_elo_h = round(elo_h + total_change)
        new_elo_a = round(elo_a - total_change)
        
        ws_teams.update_cell(cell_h.row, 2, new_elo_h)
        ws_teams.update_cell(cell_a.row, 2, new_elo_a)
        st.toast(f"📊 {home_team} {new_elo_h}({int(total_change):+})")
    except:
        pass

def run_admin_settlement():
    # 정산은 관리자만 하므로 API 호출 좀 해도 됨
    st.info("정산 시작...")
    matches = pd.DataFrame(ws_matches.get_all_records())
    bets = pd.DataFrame(ws_bets.get_all_records())
    
    # 유저 데이터 미리 로딩 (row index 찾기용)
    users_df = pd.DataFrame(ws_users.get_all_records())
    
    if 'is_settled' not in matches.columns:
        st.error("'is_settled' 헤더 없음")
        return

    matches['is_settled'] = matches['is_settled'].astype(str)
    targets = matches[(matches['status'] == 'FINISHED') & (matches['is_settled'] != 'TRUE')]

    if targets.empty:
        st.warning("정산할 경기 없음")
        return

    success_cnt = 0
    for idx, match in targets.iterrows():
        mid = match['match_id']
        res = match['result']
        
        # ELO 업데이트
        if ws_teams:
            h_xg = float(match.get('h_xg', 0) or 0)
            a_xg = float(match.get('a_xg', 0) or 0)
            h_pass = float(match.get('h_pass', 0) or 0)
            a_pass = float(match.get('a_pass', 0) or 0)
            h_ppda = float(match.get('h_ppda', 0) or 0)
            a_ppda = float(match.get('a_ppda', 0) or 0)
            
            update_team_elo_advanced(match['home'], match['away'], res, h_xg, a_xg, h_pass, a_pass, h_ppda, a_ppda)
            
        # 배당금 지급
        odds = float(match['home_odds']) if res == 'HOME' else (float(match['draw_odds']) if res == 'DRAW' else float(match['away_odds']))
        
        match_bets = bets[bets['match_id'] == mid]
        for b_idx, bet in match_bets.iterrows():
            if str(bet['choice']) == str(res):
                win_amt = int(bet['amount'] * odds)
                # 여기서도 최적화 함수 사용
                update_balance_optimized(bet['nickname'], win_amt, users_df)
                st.success(f" -> {bet['nickname']} +{win_amt}P")
        
        # 정산 완료 마킹
        try:
            # is_settled가 15번째 열이라고 가정 (헤더 순서 중요)
            row_idx = idx + 2 # 데이터프레임 인덱스 -> 시트 행 번호
            ws_matches.update_cell(row_idx, 15, 'TRUE') 
            success_cnt += 1
        except:
            pass
            
    st.success(f"{success_cnt}경기 정산 완료")

# --- [4] 데이터 로딩 (재시도 로직) ---

def fetch_all_data():
    """모든 데이터를 한 번에 가져와서 세션에 저장"""
    for i in range(3): # 3번 시도
        try:
            d_matches = pd.DataFrame(ws_matches.get_all_records())
            d_bets = pd.DataFrame(ws_bets.get_all_records())
            d_users = pd.DataFrame(ws_users.get_all_records()) # 유저 정보도 미리 가져옴
            return d_matches, d_bets, d_users
        except Exception as e:
            time.sleep(2)
    st.error("서버 연결 불안정. 잠시 후 새로고침하세요.")
    st.stop()

# --- [5] UI 및 앱 실행 ---

if 'nickname' not in st.session_state:
    st.session_state['nickname'] = None

# 앱 시작 시 데이터 로딩 (딱 1번만)
if 'db_matches' not in st.session_state:
    with st.spinner("서버 연결 중..."):
        m, b, u = fetch_all_data()
        st.session_state['db_matches'] = m
        st.session_state['db_bets'] = b
        st.session_state['db_users'] = u

# 새로고침 버튼
if st.button("🔄 데이터 동기화"):
    with st.spinner("동기화 중..."):
        m, b, u = fetch_all_data()
        st.session_state['db_matches'] = m
        st.session_state['db_bets'] = b
        st.session_state['db_users'] = u
        st.rerun()

# 변수 할당
df_matches = st.session_state['db_matches']
all_bets_data = st.session_state['db_bets']
df_users = st.session_state['db_users']

# 사이드바
with st.sidebar:
    st.title("⚽ 메뉴")
    tab1, tab2 = st.tabs(["유저", "관리자"])
    
    with tab1:
        if not st.session_state['nickname']:
            mode = st.radio("모드", ["로그인", "회원가입"], horizontal=True)
            nick = st.text_input("닉네임")
            if st.button("확인"):
                if not nick:
                    st.warning("닉네임 입력 필수")
                else:
                    # 로컬 데이터에서 확인 (API 호출 X)
                    exists = nick in df_users['nickname'].astype(str).values
                    
                    if mode == "로그인":
                        if exists:
                            st.session_state['nickname'] = nick
                            st.success("접속 성공!")
                            st.rerun()
                        else:
                            st.error("없는 닉네임")
                    else:
                        if exists:
                            st.error("이미 있음")
                        else:
                            create_new_user(nick)
                            st.session_state['nickname'] = nick
                            st.success("가입 완료! (새로고침 해주세요)")
                            # 가입 시에는 어쩔 수 없이 리로드 유도
        else:
            # 로그인 상태
            curr_nick = st.session_state['nickname']
            # 잔액도 로컬 데이터에서 조회
            try:
                my_bal = df_users.loc[df_users['nickname']==curr_nick, 'balance'].values[0]
            except:
                my_bal = 0
            
            st.info(f"👤 {curr_nick}님")
            st.metric("잔액", f"{int(my_bal):,} P")
            
            if st.button("로그아웃"):
                st.session_state.clear()
                st.rerun()
                
    with tab2:
        pw = st.text_input("관리자 비번", type="password")
        if pw == "fineplay1234":
            if st.button("💰 정산 실행"):
                run_admin_settlement()
            
            # 경기 등록 UI (간소화)
            if ws_teams:
                try:
                    teams = pd.DataFrame(ws_teams.get_all_records())
                    t_list = teams['team_name'].tolist()
                    c1, c2 = st.columns(2)
                    h = c1.selectbox("홈", t_list, key='h')
                    a = c2.selectbox("원정", t_list, index=1, key='a')
                    
                    h_elo = teams[teams['team_name']==h]['elo'].values[0]
                    a_elo = teams[teams['team_name']==a]['elo'].values[0]
                    oh, od, oa = calculate_auto_odds(h_elo, a_elo)
                    st.caption(f"배당: {oh}/{od}/{oa}")
                    
                    if st.button("경기 등록"):
                        nid = f"M{int(time.time())}"
                        ws_matches.append_row([nid, h, a, oh, od, oa, "WAITING", "", "", "", "", "", "", "", "FALSE"])
                        st.success("등록됨")
                except:
                    st.error("팀 데이터 오류")

# 메인 화면
st.title("🏆 DDC 캠퍼스 컵")

if not st.session_state['nickname']:
    st.warning("로그인이 필요합니다.")
    st.stop()

tab_bet, tab_rank = st.tabs(["🔥 베팅", "🏆 랭킹"])

with tab_bet:
    active = df_matches[df_matches['status'] == 'WAITING'] if not df_matches.empty else pd.DataFrame()
    
    # 내 베팅 (로컬 필터링)
    my_bets = all_bets_data[all_bets_data['nickname'].astype(str) == str(st.session_state['nickname'])]
    bet_ids = my_bets['match_id'].tolist()
    
    if active.empty:
        st.info("경기 없음")
    else:
        MIN, MAX = 500, 1000
        curr_nick = st.session_state['nickname']
        # 잔액 조회 (로컬)
        try:
            curr_bal = int(df_users.loc[df_users['nickname']==curr_nick, 'balance'].values[0])
        except:
            curr_bal = 0
            
        for idx, match in active.iterrows():
            mid = match['match_id']
            with st.container(border=True):
                st.subheader(f"{match['home']} vs {match['away']}")
                c1, c2, c3 = st.columns(3)
                c1.metric("승", match['home_odds'])
                c2.metric("무", match['draw_odds'])
                c3.metric("패", match['away_odds'])
                
                if mid in bet_ids:
                    # 이미 베팅함
                    rec = my_bets[my_bets['match_id'] == mid].iloc[0]
                    st.success(f"참여 완료: {rec['choice']} ({rec['amount']}P)")
                else:
                    st.markdown("---")
                    if curr_bal < MIN:
                        st.error("잔액 부족")
                    else:
                        sel = st.radio("선택", ["HOME", "DRAW", "AWAY"], key=f"s_{mid}", horizontal=True)
                        limit = min(MAX, curr_bal)
                        amt = st.number_input(f"금액", MIN, limit, step=100, key=f"m_{mid}")
                        
                        if st.button("베팅하기", key=f"b_{mid}"):
                            # 1. API 호출 최적화 함수 사용
                            place_bet_optimized(curr_nick, mid, sel, amt, df_users)
                            
                            # 2. 로컬 데이터 강제 업데이트 (화면 갱신용)
                            # 베팅 내역 추가
                            new_row = {'nickname': curr_nick, 'match_id': mid, 'choice': sel, 'amount': amt, 'timestamp': str(datetime.now())}
                            st.session_state['db_bets'] = pd.concat([st.session_state['db_bets'], pd.DataFrame([new_row])], ignore_index=True)
                            
                            # 유저 잔액 차감
                            st.session_state['db_users'].loc[st.session_state['db_users']['nickname']==curr_nick, 'balance'] -= amt
                            
                            st.success("완료!")
                            time.sleep(0.5)
                            st.rerun()

with tab_rank:
    if not df_users.empty:
        rank = df_users.sort_values('balance', ascending=False).head(10).reset_index(drop=True)
        rank.index += 1
        st.dataframe(rank[['nickname', 'balance']], use_container_width=True)
