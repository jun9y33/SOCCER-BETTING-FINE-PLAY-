import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import pandas as pd

# --- [1] 구글 시트 연결 설정 (줄바꿈 문자 해결 버전) ---
@st.cache_resource
def init_connection():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    
    # [핵심 수정] st.secrets 데이터를 수정 가능한 딕셔너리로 변환
    key_dict = dict(st.secrets["gcp_service_account"])
    
    # [핵심 수정] 잘못된 줄바꿈 문자(\\n)를 진짜 줄바꿈(\n)으로 교체
    key_dict["private_key"] = key_dict["private_key"].replace("\\n", "\n")
    
    # 수정된 key_dict를 사용하여 인증 (st.secrets를 직접 쓰면 안됨!)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(key_dict, scope)
    client = gspread.authorize(creds)
    return client

client = init_connection()

# --- [2] 시트 연결 ---
# 본인의 구글 시트 주소로 교체하세요!
url = "https://docs.google.com/spreadsheets/d/1Q4YJBhdUEHwYdMFMSFqbhyNG73z6l2rCObsKALol7IM/edit?gid=0#gid=0" 

try:
    sh = client.open_by_url(url)
except Exception as e:
    st.error(f"시트 연결 실패! 공유 설정과 URL을 확인하세요. 에러내용: {e}")
    st.stop()

ws_users = sh.worksheet("Users")
ws_matches = sh.worksheet("Matches")
ws_bets = sh.worksheet("Bets")

# --- [3] 함수 정의 ---

def get_user_data(nickname):
    users = ws_users.get_all_records()
    for user in users:
        if str(user['nickname']) == str(nickname):
            return user
    
    new_user = {'nickname': nickname, 'balance': 10000}
    ws_users.append_row([nickname, 10000])
    return new_user

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

# --- [4] UI 디자인 ---
st.set_page_config(page_title="캠퍼스 토토 (Live)", page_icon="⚽")
st.title("⚽ 캠퍼스 챔피언스리그 토토")

# 로그인 섹션
with st.sidebar:
    st.header("로그인")
    nickname = st.text_input("닉네임(ID)을 입력하세요")
    
    if nickname:
        user_info = get_user_data(nickname)
        st.success(f"환영합니다, {nickname}님!")
        st.metric("내 보유 포인트", f"{user_info['balance']:,} P")
        
        if st.button("새로고침"):
            st.rerun()
    else:
        st.warning("닉네임을 입력해야 베팅할 수 있습니다.")
        st.stop()

# 메인 경기 목록 로딩
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
            col1.metric(f"{match['home']} 승", match['home_odds'])
            col2.metric("무승부", match['draw_odds'])
            col3.metric(f"{match['away']} 승", match['away_odds'])
            
            with st.expander("베팅하기"):
                choice = st.radio(
                    "선택", 
                    ['HOME', 'DRAW', 'AWAY'], 
                    key=f"choice_{match['match_id']}",
                    horizontal=True
                )
                amount = st.number_input(
                    "금액", 
                    min_value=100, 
                    max_value=user_info['balance'], 
                    step=100,
                    key=f"amount_{match['match_id']}"
                )
                
                if st.button("베팅 확정", key=f"btn_{match['match_id']}"):
                    if amount > user_info['balance']:
                        st.error("잔액이 부족합니다.")
                    else:
                        with st.spinner("베팅 기록 중..."):
                            place_bet(nickname, match['match_id'], choice, amount)
                        st.success(f"✅ 베팅 완료!")
                        st.rerun()

            st.markdown("---")

# 내 베팅 기록
st.subheader("📜 나의 베팅 기록")
all_bets = ws_bets.get_all_records()
my_bets = [bet for bet in all_bets if str(bet['nickname']) == str(nickname)]

if my_bets:
    st.table(pd.DataFrame(my_bets)[['match_id', 'choice', 'amount', 'timestamp']])
else:
    st.text("아직 베팅 내역이 없습니다.")
