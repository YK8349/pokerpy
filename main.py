
import tkinter as tk
from tkinter import simpledialog, messagebox, font
import random
import google.generativeai as genai
import itertools
import json
import threading
import queue

# --- Gemini APIのセットアップ ---
GOOGLE_API_KEY = "" #ENTER YOUR API KEY
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    model = genai.GenerativeModel('gemini-2.5-flash')
else:
    print("警告: GOOGLE_API_KEYが.envファイルに設定されていません。Geminiプレイヤーは使用できません。")
    model = None

# --- ゲームロジック（コア部分） ---

SUITS = ['♠', '♥', '♦', '♣']
RANKS = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
RANK_VALUES = {'2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9, '10': 10, 'J': 11, 'Q': 12, 'K': 13, 'A': 14}

class Card:
    def __init__(self, suit, rank):
        self.suit = suit
        self.rank = rank
        self.value = RANK_VALUES[rank]

    def __str__(self):
        return f"{self.suit}{self.rank}"

class Deck:
    def __init__(self):
        self.cards = [Card(s, r) for s in SUITS for r in RANKS]
        self.shuffle()

    def shuffle(self):
        random.shuffle(self.cards)

    def deal(self):
        return self.cards.pop() if self.cards else None

class Player:
    def __init__(self, name, chips=1000, is_cpu=False, is_gemini=False):
        self.name = name
        self.hand = []
        self.chips = chips
        self.bet = 0
        self.has_acted = False
        self.is_folded = False
        self.is_all_in = False
        self.is_cpu = is_cpu
        self.is_gemini = is_gemini
        # GUI表示用の手札公開フラグ
        self.show_hand = False

class PokerGame:
    def __init__(self, app, human_player_name, cpu_players=0, gemini_players=0):
        self.app = app
        self.players = []
        self.deck = Deck()
        self.community_cards = []
        self.pot = 0
        self.current_bet = 0
        self.current_player_index = 0
        self.game_stage = "pre-flop"
        self.game_in_progress = False
        self.small_blind_index = -1
        self.big_blind_index = -1
        self.small_blind_amount = 10
        self.big_blind_amount = 20
        self.action_queue = queue.Queue()

        # プレイヤーの追加
        self.add_player(human_player_name)
        for i in range(cpu_players):
            self.add_player(f"CPU {i+1}", is_cpu=True)
        if model:
            for i in range(gemini_players):
                self.add_player(f"Gemini {i+1}", is_gemini=True)

    def add_player(self, name, is_cpu=False, is_gemini=False):
        self.players.append(Player(name, is_cpu=is_cpu, is_gemini=is_gemini))

    def start_game(self):
        if len(self.players) < 2:
            self.app.log("プレイヤーが2人未満のため、ゲームを開始できません。")
            return
        self.game_in_progress = True
        self.small_blind_index = (self.small_blind_index + 1) % len(self.players)
        self.start_round()

    def start_round(self):
        self.game_in_progress = True
        self.deck = Deck()
        self.community_cards = []
        self.pot = 0
        self.current_bet = 0
        self.game_stage = "pre-flop"

        self.players = [p for p in self.players if p.chips > 0]
        if len(self.players) < 2:
            self.app.log("プレイ可能なプレイヤーが2人未満になりました。ゲームを終了します。")
            self.app.show_end_game_options()
            return

        for player in self.players:
            player.hand = [self.deck.deal(), self.deck.deal()]
            player.bet = 0
            player.has_acted = False
            player.is_folded = False
            player.is_all_in = False
            player.show_hand = isinstance(player, Player) and not player.is_cpu and not player.is_gemini

        self.small_blind_index = (self.small_blind_index + 1) % len(self.players)
        self.big_blind_index = (self.small_blind_index + 1) % len(self.players)

        sb_player = self.players[self.small_blind_index]
        bb_player = self.players[self.big_blind_index]
        
        self.app.log(f"{sb_player.name}がスモールブラインド {self.small_blind_amount} をベット。")
        sb_player.bet = min(self.small_blind_amount, sb_player.chips)
        sb_player.chips -= sb_player.bet
        self.pot += sb_player.bet
        if sb_player.chips == 0: sb_player.is_all_in = True

        self.app.log(f"{bb_player.name}がビッグブラインド {self.big_blind_amount} をベット。")
        bb_player.bet = min(self.big_blind_amount, bb_player.chips)
        bb_player.chips -= bb_player.bet
        self.pot += bb_player.bet
        if bb_player.chips == 0: bb_player.is_all_in = True
        
        self.current_bet = self.big_blind_amount
        self.current_player_index = (self.big_blind_index + 1) % len(self.players)
        
        self.start_betting_round()

    def start_betting_round(self):
        if self.game_stage != "pre-flop":
            self.current_player_index = (self.small_blind_index) % len(self.players)
            while self.players[self.current_player_index].is_folded or self.players[self.current_player_index].is_all_in:
                self.current_player_index = (self.current_player_index + 1) % len(self.players)

            self.current_bet = 0
            for p in self.players:
                if not p.is_folded and not p.is_all_in:
                    p.has_acted = False
                p.bet = 0
        
        self.process_turn()

    def process_turn(self):
        self.app.update_display()

        active_players = [p for p in self.players if not p.is_folded]
        if len(active_players) <= 1:
            self.app.root.after(1000, self.end_round)
            return

        active_not_allin = [p for p in active_players if not p.is_all_in]
        acted_players = [p for p in active_not_allin if p.has_acted]
        bets = {p.bet for p in active_not_allin}

        if len(acted_players) == len(active_not_allin) and len(bets) <= 1:
            self.app.root.after(1000, self.end_betting_round)
            return

        current_player = self.players[self.current_player_index]
        if current_player.is_folded or current_player.is_all_in:
            self.current_player_index = (self.current_player_index + 1) % len(self.players)
            self.app.root.after(100, self.process_turn)
            return

        self.app.update_display()
        
        if current_player.is_cpu:
            self.app.log(f"{current_player.name}のターン...")
            self.app.root.after(1500, self.get_cpu_action, current_player)
        elif current_player.is_gemini:
            self.app.log(f"{current_player.name} (Gemini) が思考中です...")
            threading.Thread(target=self.get_gemini_poker_action, args=(current_player,), daemon=True).start()
            self.app.root.after(100, self.check_gemini_queue)
        else: # Human player
            self.app.log(f"あなたのターンです。")
            self.app.enable_action_buttons()

    def get_cpu_action(self, player):
        amount_to_call = self.current_bet - player.bet
        if amount_to_call > 0:
            if amount_to_call >= player.chips:
                self.handle_action('call')
            else:
                if random.random() < 0.7: self.handle_action('call')
                else: self.handle_action('fold')
        else:
            self.handle_action('check')

    def check_gemini_queue(self):
        try:
            action_data = self.action_queue.get_nowait()
            action = action_data.get("action")
            amount = action_data.get("amount", 0)
            self.app.log(f"Geminiのアクション: {action} {amount if action == 'raise' else ''}")
            self.handle_action(action, amount)
        except queue.Empty:
            self.app.root.after(100, self.check_gemini_queue)

    def get_gemini_poker_action(self, player):
        hand_str = ' '.join(map(str, player.hand))
        community_str = ' '.join(map(str, self.community_cards))
        player_states = [{"name": p.name, "chips": p.chips, "bet": p.bet, "is_folded": p.is_folded, "is_all_in": p.is_all_in, "is_me": p == player} for p in self.players]
        amount_to_call = self.current_bet - player.bet
        min_raise = self.current_bet * 2 if self.current_bet > 0 else self.big_blind_amount

        prompt = f"""
            あなたはプロのテキサスホールデムポーカープレイヤーです。
            以下のゲーム状況を分析し、あなたの取るべき最適なアクションをJSON形式で出力してください。
            # ゲーム状況
            - ゲームステージ: {self.game_stage}
            - あなたの手札: {hand_str}
            - コミュニティカード: {community_str or "なし"}
            - ポット合計: {self.pot}
            - 現在のラウンドでのあなたのベット額: {player.bet}
            - 現在のコールに必要な合計ベット額: {self.current_bet}
            - あなたの残りチップ: {player.chips}
            - プレイヤーの状態: {json.dumps(player_states, indent=2, ensure_ascii=False)}
            # あなたが実行可能なアクション
            - `fold`: ゲームから降ります。
            - `check`: 追加のベットをせずに行動を次のプレイヤーに回します。（コール不要の場合のみ）
            - `call`: 現在のベット額までチップを追加で出します。コールに必要な額は `{amount_to_call}` です。
            - `raise`: 現在のベット額をさらに引き上げます。`amount`にレイズ後の合計ベット額を指定してください。最低レイズ額は `{min_raise}` です。
            - `all-in`: あなたの持っているチップをすべてベットします。
            # 注意事項
            - JSONは必ず `action` と、レイズの場合は `amount` キーを含めてください。
            - `amount`はレイズ後の合計ベット額です。追加する額ではありません。
            - 最終的な出力はJSONオブジェクトのみにしてください。
            ```json
            {{
            "action": "...",
            "amount": ...
            }}
            ```
            """
        try:
            response = model.generate_content(prompt)
            json_part = response.text[response.text.find('{'):response.text.rfind('}')+1]
            action_data = json.loads(json_part)
            
            action = action_data.get("action")
            amount = action_data.get("amount", 0)
            can_check = amount_to_call <= 0

            final_action = 'fold'
            final_amount = 0

            if action == 'fold': final_action = 'fold'
            elif action == 'check': final_action = 'check' if can_check else 'call'
            elif action == 'call': final_action = 'check' if can_check else 'call'
            elif action == 'raise':
                amount = min(amount, player.chips + player.bet)
                if amount < min_raise and player.chips + player.bet > min_raise: amount = min_raise
                final_action = 'raise' if amount > self.current_bet else 'call'
                final_amount = amount
            elif action == 'all-in':
                final_action = 'raise'
                final_amount = player.chips + player.bet
            
            self.action_queue.put({"action": final_action, "amount": final_amount})

        except Exception as e:
            print(f"Gemini action error: {e}")
            self.action_queue.put({"action": "fold", "amount": 0})

    def end_betting_round(self):
        for p in self.players:
            self.pot += p.bet
            p.bet = 0
        
        if self.game_stage == "pre-flop":
            self.game_stage = "flop"
            self.community_cards.extend([self.deck.deal() for _ in range(3)])
            self.app.log(f"--- フロップ ---")
        elif self.game_stage == "flop":
            self.game_stage = "turn"
            self.community_cards.append(self.deck.deal())
            self.app.log(f"--- ターン ---")
        elif self.game_stage == "turn":
            self.game_stage = "river"
            self.community_cards.append(self.deck.deal())
            self.app.log(f"--- リバー ---")
        elif self.game_stage == "river":
            self.game_stage = "showdown"
            self.app.root.after(1000, self.end_round)
            return
        
        self.app.log(f"コミュニティカード: {' '.join(map(str, self.community_cards))}")
        self.app.root.after(1000, self.start_betting_round)

    def handle_action(self, action, amount=0):
        player = self.players[self.current_player_index]
        
        if action == 'fold':
            player.is_folded = True
            self.app.log(f"{player.name}がフォールドしました。")
        elif action == 'check':
            self.app.log(f"{player.name}がチェックしました。")
        elif action == 'call':
            amount_to_call = self.current_bet - player.bet
            if amount_to_call >= player.chips:
                self.app.log(f"{player.name}がオールインしました。")
                player.bet += player.chips
                player.chips = 0
                player.is_all_in = True
            else:
                self.app.log(f"{player.name}が{amount_to_call}コールしました。")
                player.chips -= amount_to_call
                player.bet += amount_to_call
        elif action == 'raise':
            if amount >= player.chips + player.bet:
                amount = player.chips + player.bet
                player.is_all_in = True
                self.app.log(f"{player.name}がオールインレイズ！ ({amount})")
            else:
                self.app.log(f"{player.name}が{amount}にレイズしました。")

            amount_to_raise = amount - player.bet
            player.chips -= amount_to_raise
            player.bet = amount
            self.current_bet = player.bet
            for p in self.players:
                if p != player and not p.is_folded and not p.is_all_in:
                    p.has_acted = False
        
        player.has_acted = True
        self.current_player_index = (self.current_player_index + 1) % len(self.players)
        self.app.root.after(1000, self.process_turn)

    def evaluate_hand(self, hand):
        all_hands = list(itertools.combinations(hand, 5))
        best_hand_rank = (-1, [])
        for h in all_hands:
            rank = self.get_hand_rank(h)
            if rank[0] > best_hand_rank[0] or (rank[0] == best_hand_rank[0] and sorted([c.value for c in rank[1]], reverse=True) > sorted([c.value for c in best_hand_rank[1]], reverse=True)):
                best_hand_rank = rank
        return best_hand_rank

    def get_hand_rank(self, hand):
        hand = sorted(hand, key=lambda card: card.value, reverse=True)
        values = [c.value for c in hand]; suits = [c.suit for c in hand]
        is_flush = len(set(suits)) == 1
        is_straight = (len(set(values)) == 5 and max(values) - min(values) == 4) or (values == [14, 5, 4, 3, 2])
        if is_straight and is_flush: return (8, hand) if values != [14, 13, 12, 11, 10] else (9, hand)
        counts = sorted({v: values.count(v) for v in set(values)}.items(), key=lambda item: (item[1], item[0]), reverse=True)
        if counts[0][1] == 4: return (7, sorted(hand, key=lambda c: (c.value != counts[0][0], c.value), reverse=True))
        if counts[0][1] == 3 and counts[1][1] == 2: return (6, hand)
        if is_flush: return (5, hand)
        if is_straight: return (4, [c for c in hand if c.value != 14] + [c for c in hand if c.value == 14]) if values == [14, 5, 4, 3, 2] else (4, hand)
        if counts[0][1] == 3: return (3, sorted(hand, key=lambda c: (c.value != counts[0][0], c.value), reverse=True))
        if counts[0][1] == 2 and counts[1][1] == 2: return (2, sorted(hand, key=lambda c: (c.value != counts[0][0] and c.value != counts[1][0], c.value), reverse=True))
        if counts[0][1] == 2: return (1, sorted(hand, key=lambda c: (c.value != counts[0][0], c.value), reverse=True))
        return (0, hand)

    def end_round(self):
        self.pot += sum(p.bet for p in self.players)
        for p in self.players: p.bet = 0

        active_players = [p for p in self.players if not p.is_folded]
        
        self.app.log("--- ラウンド終了 ---")
        for p in self.players: p.show_hand = True
        self.app.update_display()

        if len(active_players) == 1:
            winner = active_players[0]
            winner.chips += self.pot
            self.app.log(f"{winner.name} の勝利！ポット ({self.pot}) を獲得。")
        else:
            winner_data = sorted([{"player": p, "rank": self.evaluate_hand(p.hand + self.community_cards)} for p in active_players], key=lambda x: (x["rank"][0], [c.value for c in x["rank"][1]]), reverse=True)
            best_rank_tuple = (winner_data[0]["rank"][0], [c.value for c in winner_data[0]["rank"][1]])
            winners = [d for d in winner_data if (d["rank"][0], [c.value for c in d["rank"][1]]) == best_rank_tuple]
            
            winnings = self.pot // len(winners)
            for w_data in winners:
                w_data["player"].chips += winnings
            
            hand_names = ["ハイカード", "ワンペア", "ツーペア", "スリーカード", "ストレート", "フラッシュ", "フルハウス", "フォーカード", "ストレートフラッシュ", "ロイヤルフラッシュ"]
            win_hand_name = hand_names[winner_data[0]["rank"][0]]
            win_hand_str = ' '.join(map(str, winner_data[0]["rank"][1]))
            winner_names = ", ".join([w["player"].name for w in winners])
            
            self.app.log(f"{winner_names} の勝利！ポット ({self.pot}) を獲得。")
            self.app.log(f"役: {win_hand_name} ({win_hand_str})")
            
            for p in active_players:
                p_rank = self.evaluate_hand(p.hand + self.community_cards)
                self.app.log(f"  - {p.name}: {' '.join(map(str, p.hand))} ({hand_names[p_rank[0]]})")

        self.game_in_progress = False
        self.app.show_end_game_options()

# --- GUIアプリケーション ---

class PokerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("ポーカーゲーム")
        self.root.geometry("900x700")
        self.root.configure(bg="#0d3d14")

        self.game = None
        
        # フォント設定
        self.default_font = font.Font(family="Yu Gothic UI", size=10)
        self.log_font = font.Font(family="Yu Gothic UI", size=11)
        self.card_font = font.Font(family="Arial", size=16, weight="bold")
        self.title_font = font.Font(family="Yu Gothic UI", size=16, weight="bold")

        self.create_setup_frame()

    def create_setup_frame(self):
        self.setup_frame = tk.Frame(self.root, bg="#0d3d14")
        self.setup_frame.pack(pady=20, padx=20, fill="both", expand=True)

        tk.Label(self.setup_frame, text="ポーカーゲームへようこそ", font=self.title_font, fg="white", bg="#0d3d14").pack(pady=20)
        
        tk.Label(self.setup_frame, text="あなたの名前:", fg="white", bg="#0d3d14").pack()
        self.name_entry = tk.Entry(self.setup_frame, font=self.default_font)
        self.name_entry.pack(pady=5)
        self.name_entry.insert(0, "Player 1")

        tk.Label(self.setup_frame, text="CPUプレイヤーの数:", fg="white", bg="#0d3d14").pack()
        self.cpu_entry = tk.Entry(self.setup_frame, font=self.default_font)
        self.cpu_entry.pack(pady=5)
        self.cpu_entry.insert(0, "1")

        tk.Label(self.setup_frame, text="Geminiプレイヤーの数:", fg="white", bg="#0d3d14").pack()
        self.gemini_entry = tk.Entry(self.setup_frame, font=self.default_font)
        self.gemini_entry.pack(pady=5)
        self.gemini_entry.insert(0, "1" if model else "0")
        if not model:
            self.gemini_entry.config(state="disabled")

        start_button = tk.Button(self.setup_frame, text="ゲーム開始", command=self.start_game_from_setup, font=self.default_font, bg="#4CAF50", fg="white")
        start_button.pack(pady=20)

    def start_game_from_setup(self):
        name = self.name_entry.get() or "Player 1"
        try:
            cpu_count = int(self.cpu_entry.get() or 0)
            gemini_count = int(self.gemini_entry.get() or 0)
            if cpu_count + gemini_count > 7:
                messagebox.showerror("エラー", "CPUとGeminiの合計は7人以下にしてください。")
                return
            if cpu_count + gemini_count < 1:
                messagebox.showerror("エラー", "対戦相手を1人以上指定してください。")
                return
        except ValueError:
            messagebox.showerror("エラー", "プレイヤー数には数値を入力してください。")
            return

        self.setup_frame.destroy()
        self.create_game_frame()
        self.game = PokerGame(self, name, cpu_count, gemini_count)
        self.game.start_game()

    def create_game_frame(self):
        # メインフレーム
        main_frame = tk.Frame(self.root, bg="#0d3d14")
        main_frame.pack(fill="both", expand=True, padx=10, pady=10)

        # 上部フレーム (テーブル)
        table_frame = tk.Frame(main_frame, bg="#0d3d14")
        table_frame.pack(fill="both", expand=True)

        self.player_frames = {}
        # プレイヤーの位置を円形に配置
        player_positions = [
            (0.5, 0.85), (0.15, 0.7), (0.1, 0.4), (0.15, 0.1),
            (0.5, 0.05), (0.85, 0.1), (0.9, 0.4), (0.85, 0.7)
        ]

        for i in range(8): # 最大8人分のフレームを事前に用意
            p_frame = tk.Frame(table_frame, bg="#1a5221", relief="raised", borderwidth=2)
            name_label = tk.Label(p_frame, text="", font=self.default_font, bg="#1a5221", fg="white")
            name_label.pack(pady=(5,0))
            chips_label = tk.Label(p_frame, text="", font=self.default_font, bg="#1a5221", fg="white")
            chips_label.pack()
            hand_label = tk.Label(p_frame, text="", font=self.card_font, bg="#1a5221", fg="white")
            hand_label.pack(pady=(0,5))
            self.player_frames[i] = {
                "frame": p_frame, "name": name_label, "chips": chips_label, "hand": hand_label
            }

        # 中央情報フレーム
        center_frame = tk.Frame(table_frame, bg="#0d3d14")
        center_frame.place(relx=0.5, rely=0.45, anchor="center")
        
        self.pot_label = tk.Label(center_frame, text="Pot: 0", font=self.title_font, fg="yellow", bg="#0d3d14")
        self.pot_label.pack(pady=10)
        self.community_label = tk.Label(center_frame, text="", font=self.card_font, fg="white", bg="#0d3d14")
        self.community_label.pack(pady=10)

        # 下部フレーム (アクションとログ)
        bottom_frame = tk.Frame(main_frame, bg="#0d3d14")
        bottom_frame.pack(fill="x", side="bottom")

        # アクションフレーム
        action_frame = tk.Frame(bottom_frame, bg="#0d3d14")
        action_frame.pack(pady=10)
        
        self.action_buttons = {
            "check": tk.Button(action_frame, text="チェック", command=lambda: self.handle_player_action('check'), state="disabled", font=self.default_font),
            "call": tk.Button(action_frame, text="コール", command=lambda: self.handle_player_action('call'), state="disabled", font=self.default_font),
            "raise": tk.Button(action_frame, text="レイズ", command=self.prompt_for_raise, state="disabled", font=self.default_font),
            "fold": tk.Button(action_frame, text="フォールド", command=lambda: self.handle_player_action('fold'), state="disabled", font=self.default_font)
        }
        self.action_buttons["check"].pack(side="left", padx=5)
        self.action_buttons["call"].pack(side="left", padx=5)
        self.action_buttons["raise"].pack(side="left", padx=5)
        self.action_buttons["fold"].pack(side="left", padx=5)

        # ログフレーム
        log_frame = tk.Frame(bottom_frame, height=150)
        log_frame.pack(fill="x", expand=True)
        self.log_text = tk.Text(log_frame, height=8, state="disabled", bg="black", fg="lightgreen", font=self.log_font, relief="sunken", borderwidth=1)
        scrollbar = tk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.config(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)

    def update_display(self):
        if not self.game: return

        # プレイヤー情報を更新
        player_positions = [
            (0.5, 0.85), (0.15, 0.7), (0.1, 0.4), (0.15, 0.1),
            (0.5, 0.05), (0.85, 0.1), (0.9, 0.4), (0.85, 0.7)
        ]
        
        for i, p_frame_info in self.player_frames.items():
            p_frame_info["frame"].place_forget()

        for i, player in enumerate(self.game.players):
            pos = player_positions[i]
            info = self.player_frames[i]
            frame = info["frame"]
            
            frame.place(relx=pos[0], rely=pos[1], anchor="center")

            status = ""
            if player.is_folded: status = " (Fold)"
            elif player.is_all_in: status = " (All-in)"
            
            info["name"].config(text=f"{player.name}{status}")
            info["chips"].config(text=f"Chips: {player.chips}Bet: {player.bet}")
            
            if player.show_hand:
                hand_str = ' '.join(map(str, player.hand))
                card_color = "cyan" if not player.is_cpu and not player.is_gemini else "white"
                info["hand"].config(text=hand_str, fg=card_color)
            else:
                info["hand"].config(text="🂠 🂠", fg="white")

            # 現在のプレイヤーをハイライト
            if self.game.game_in_progress and i == self.game.current_player_index:
                frame.config(bg="#e8b422") # Gold
                for widget in frame.winfo_children(): widget.config(bg="#e8b422")
            else:
                frame.config(bg="#1a5221") # Green
                for widget in frame.winfo_children(): widget.config(bg="#1a5221")

        # ポットとコミュニティカードを更新
        self.pot_label.config(text=f"Pot: {self.game.pot + sum(p.bet for p in self.game.players)}")
        self.community_label.config(text=' '.join(map(str, self.game.community_cards)))
        self.root.update_idletasks()

    def enable_action_buttons(self):
        player = self.game.players[self.game.current_player_index]
        amount_to_call = self.game.current_bet - player.bet
        
        self.action_buttons["fold"].config(state="normal")
        
        if amount_to_call <= 0: # チェック可能
            self.action_buttons["check"].config(state="normal")
            self.action_buttons["call"].config(state="disabled")
        else: # コール必要
            self.action_buttons["check"].config(state="disabled")
            self.action_buttons["call"].config(state="normal", text=f"Call ({amount_to_call})")
            if player.chips <= amount_to_call:
                self.action_buttons["call"].config(text="All-in")

        if player.chips > amount_to_call:
            self.action_buttons["raise"].config(state="normal")
        else:
            self.action_buttons["raise"].config(state="disabled")

    def disable_action_buttons(self):
        for button in self.action_buttons.values():
            button.config(state="disabled")

    def handle_player_action(self, action):
        self.disable_action_buttons()
        self.game.handle_action(action)

    def prompt_for_raise(self):
        player = self.game.players[self.game.current_player_index]
        min_raise = self.game.current_bet * 2 if self.game.current_bet > 0 else self.game.big_blind_amount
        max_raise = player.chips + player.bet
        
        amount = simpledialog.askinteger(
            "レイズ",
            f"レイズ後の合計ベット額を入力してください。最小: {min_raise}, 最大: {max_raise}",
            minvalue=min_raise,
            maxvalue=max_raise
        )
        if amount is not None:
            self.disable_action_buttons()
            self.game.handle_action('raise', amount)

    def log(self, message):
        self.log_text.config(state="normal")
        self.log_text.insert(tk.END, message + "")
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")
        self.root.update_idletasks()

    def show_end_game_options(self):
        self.disable_action_buttons()
        result = messagebox.askyesno("ゲーム終了", "もう一度プレイしますか？")
        if result:
            self.log("--- 新しいラウンドを開始します ---")
            self.game.start_round()
        else:
            self.root.quit()

if __name__ == "__main__":
    root = tk.Tk()
    app = PokerApp(root)
    root.mainloop()
