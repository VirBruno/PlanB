import random

from django.test import SimpleTestCase

from apps.plans.services import uno_game


def card(identifier, color, value):
    return {"id": identifier, "color": color, "value": value}


def state(*, player, bot=None, top=None, color="rojo", deck=None):
    return {"id": "partida", "player": player, "bot": bot or [card("b", "azul", "2")],
            "deck": deck or [card("d", "azul", "4")],
            "discard": [top or card("t", "rojo", "3")], "color": color,
            "turn": "player", "drawn": None, "winner": None, "message": ""}


class UnoGameTests(SimpleTestCase):
    def test_new_deck_has_108_unique_cards_and_seven_each(self):
        game = uno_game.new_game(rng=random.Random(42))
        cards = game["deck"] + game["player"] + game["bot"] + game["discard"]
        self.assertEqual((len(cards), len({c["id"] for c in cards})), (108, 108))
        self.assertEqual((len(game["player"]), len(game["bot"])), (7, 7))
        self.assertTrue(game["discard"][-1]["value"].isdigit())

    def test_public_state_hides_bot_hand_and_deck(self):
        game = uno_game.new_game(rng=random.Random(4))
        visible = uno_game.public_state(game)
        self.assertNotIn("bot", visible)
        self.assertNotIn("deck", visible)
        self.assertEqual(visible["bot_count"], 7)

    def test_invalid_card_and_illegal_draw_four_are_rejected(self):
        game = state(player=[card("a", "rojo", "7"), card("w", None, "mas_cuatro")])
        with self.assertRaises(uno_game.InvalidUnoMove):
            uno_game.act(game, "play", card_id="w", color="azul")
        with self.assertRaises(uno_game.InvalidUnoMove):
            uno_game.act(game, "play", card_id="otra")
        self.assertEqual(len(game["player"]), 2)

    def test_wild_requires_color_and_winning_hand_ends_game(self):
        game = state(player=[card("w", None, "comodin")])
        with self.assertRaises(uno_game.InvalidUnoMove):
            uno_game.act(game, "play", card_id="w")
        uno_game.act(game, "play", card_id="w", color="verde")
        self.assertEqual((game["winner"], game["color"]), ("player", "verde"))

    def test_drawn_playable_card_is_only_card_available(self):
        game = state(player=[card("a", "rojo", "7")], deck=[card("d", "rojo", "5")])
        uno_game.act(game, "draw", rng=random.Random(1))
        self.assertEqual(game["drawn"], "d")
        with self.assertRaises(uno_game.InvalidUnoMove):
            uno_game.act(game, "play", card_id="a")
        self.assertEqual([c["id"] for c in uno_game.public_state(game)["hand"] if c["playable"]], ["d"])

    def test_draw_two_skips_opponent_and_gives_two_cards(self):
        game = state(player=[card("a", "rojo", "mas_dos"), card("z", "verde", "1")],
                     deck=[card("d1", "verde", "2"), card("d2", "verde", "3")])
        uno_game.act(game, "play", card_id="a", rng=random.Random(1))
        self.assertEqual(len(game["bot"]), 3)
        self.assertEqual(game["turn"], "player")

    def test_automatic_uno_notice_survives_bot_response(self):
        game = state(player=[card("a", "rojo", "7"), card("z", "verde", "1")],
                     bot=[card("b1", "rojo", "4"), card("b2", "azul", "2")])
        uno_game.act(game, "play", card_id="a", rng=random.Random(1))
        visible = uno_game.public_state(game)
        self.assertEqual(visible["announcements"], [
            "¡UNO! Te queda una carta.", "¡UNO! Al bot le queda una carta.",
        ])
        self.assertIn("El bot jugó", visible["message"])
        uno_game.act(game, "draw", rng=random.Random(1))
        self.assertEqual(uno_game.public_state(game)["announcements"], [])

    def test_penalty_is_visible_even_if_bot_plays_another_card(self):
        game = state(player=[card("a", "rojo", "7"), card("p1", "verde", "1"), card("p2", "azul", "1")],
                     bot=[card("b1", "rojo", "mas_dos"), card("b2", "rojo", "5"), card("b3", "azul", "2")],
                     deck=[card("d1", "verde", "2"), card("d2", "verde", "3")])
        uno_game.act(game, "play", card_id="a", rng=random.Random(1))
        events = uno_game.public_state(game)["events"]
        self.assertEqual(len(game["player"]), 4)
        self.assertIn({"type": "draw", "actor": "player", "count": 2, "reason": "mas_dos"}, events)
        self.assertTrue(any(event.get("actor") == "bot" and event.get("value") == "5" for event in events))
        self.assertNotIn("deck", uno_game.public_state(game))

    def test_recycles_discard_when_deck_is_empty(self):
        game = state(player=[card("a", "verde", "7")])
        game["deck"] = []
        game["discard"] = [card("old", "rojo", "4"), card("top", "rojo", "3")]
        uno_game.act(game, "draw", rng=random.Random(1))
        self.assertEqual(game["drawn"], "old")
        self.assertEqual(game["discard"][-1]["id"], "top")

    def test_complete_games_preserve_the_deck_and_hide_the_bot_cards(self):
        for seed in range(20):
            rng = random.Random(seed)
            game = uno_game.new_game(rng=rng)
            for _ in range(1000):
                if game["winner"]:
                    break
                public = uno_game.public_state(game)
                self.assertNotIn("bot", public)
                self.assertNotIn("deck", public)
                playable = [card for card in public["hand"] if card["playable"]]
                if playable:
                    chosen = playable[0]
                    uno_game.act(game, "play", card_id=chosen["id"], color="rojo", rng=rng)
                elif public["drawn"]:
                    uno_game.act(game, "pass", rng=rng)
                else:
                    uno_game.act(game, "draw", rng=rng)
                cards = game["deck"] + game["player"] + game["bot"] + game["discard"]
                self.assertEqual(len(cards), 108)
                self.assertEqual(len({card["id"] for card in cards}), 108)
            self.assertIn(game["winner"], ("player", "bot"), f"La partida {seed} no terminó")
