from django.test import SimpleTestCase

from apps.plans.services.election_service import rank_proposals


class ElectionRankingTests(SimpleTestCase):
    def test_no_leader_until_a_victory_exists(self):
        ranked, leader = rank_proposals([{"id": 1, "victories": 0}], score_key="victories")
        self.assertEqual((ranked[0]["rank"], leader), (1, None))

    def test_unique_victory_leads_and_ties_share_place(self):
        proposals = [{"id": 1, "victories": 1}, {"id": 2, "victories": 3},
                     {"id": 3, "victories": 1}, {"id": 4, "victories": 0}]
        ranked, leader = rank_proposals(proposals, score_key="victories")
        self.assertEqual(leader, 2)
        self.assertEqual([(p["id"], p["rank"]) for p in ranked], [(2, 1), (1, 2), (3, 2), (4, 4)])

    def test_tied_leaders_do_not_produce_a_winner(self):
        ranked, leader = rank_proposals([{"id": 1, "victories": 2}, {"id": 2, "victories": 2}],
                                        score_key="victories")
        self.assertEqual([p["rank"] for p in ranked], [1, 1])
        self.assertIsNone(leader)

    def test_truco_still_requires_thirty_points(self):
        ranked, leader = rank_proposals([{"id": 1, "score": 29}], score_key="score", minimum_score=30)
        self.assertEqual(ranked[0]["rank"], 1)
        self.assertIsNone(leader)
