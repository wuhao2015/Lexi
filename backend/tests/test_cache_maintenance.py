import tempfile
import unittest
from pathlib import Path

from app.cache_maintenance import (
    MaintenanceSummary,
    clean_bad_cache_entries,
    merge_duplicate_candidates,
)
from app.config import Settings
from app.db import TranslationCache, User, Vocabulary, get_session_local, init_engine


class CacheMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self._tmpdir.name) / "test.db"
        init_engine(f"sqlite:///{db_path}")
        self.session = get_session_local()()
        user = User(username="u1", password_hash="x")
        self.session.add(user)
        self.session.commit()
        self.user_id = user.id

    def tearDown(self):
        self.session.close()
        self._tmpdir.cleanup()

    def test_clean_bad_cache_entries_removes_self_echo(self):
        cache = TranslationCache(
            term="neutral",
            source_lang="zh",
            target_lang="en",
            primary_translation="neutral",
            alt_translations=None,
        )
        self.session.add(cache)
        self.session.flush()
        self.session.add(
            Vocabulary(
                user_id=self.user_id,
                term="neutral",
                display_term="neutral",
                source_lang="zh",
                target_lang="en",
                cache_id=cache.id,
                priority=100,
            )
        )
        self.session.commit()

        summary = MaintenanceSummary()
        clean_bad_cache_entries(self.session, summary)
        self.session.commit()

        self.assertEqual(summary.bad_cache_deleted, 1)
        self.assertEqual(summary.bad_vocab_deleted, 1)
        self.assertEqual(self.session.query(TranslationCache).count(), 0)
        self.assertEqual(self.session.query(Vocabulary).count(), 0)

    def test_merge_same_term_group_keeps_best_quality(self):
        self.session.add_all(
            [
                TranslationCache(
                    term="Hello",
                    source_lang="en",
                    target_lang="zh",
                    primary_translation="你好",
                    alt_translations=["您好"],
                ),
                TranslationCache(
                    term=" hello ",
                    source_lang="en",
                    target_lang="zh",
                    primary_translation="你好",
                    alt_translations=["哈喽", "您好"],
                ),
            ]
        )
        self.session.commit()

        summary = MaintenanceSummary()
        settings = Settings(
            cache_conflict_policy="keep_highest_quality_score",
            cache_merge_identical_enabled=True,
        )
        merge_duplicate_candidates(self.session, settings, summary)
        self.session.commit()

        rows = self.session.query(TranslationCache).all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(summary.merged_group_count, 1)
        self.assertEqual(summary.merged_cache_deleted, 1)
        self.assertTrue(isinstance(rows[0].alt_translations, list))
        self.assertGreaterEqual(len(rows[0].alt_translations), 2)

    def test_merge_same_primary_across_terms_repoints_vocab(self):
        winner = TranslationCache(
            term="修图",
            source_lang="zh",
            target_lang="en",
            primary_translation="edit photos",
            alt_translations=["photo editing"],
        )
        loser = TranslationCache(
            term="图片编辑",
            source_lang="zh",
            target_lang="en",
            primary_translation="Edit Photos",
            alt_translations=["image editing"],
        )
        self.session.add_all([winner, loser])
        self.session.flush()

        self.session.add(
            Vocabulary(
                user_id=self.user_id,
                term="图片编辑",
                display_term="图片编辑",
                source_lang="zh",
                target_lang="en",
                cache_id=loser.id,
                priority=120,
            )
        )
        self.session.commit()

        summary = MaintenanceSummary()
        settings = Settings(
            cache_conflict_policy="keep_highest_quality_score",
            cache_merge_identical_enabled=True,
        )
        merge_duplicate_candidates(self.session, settings, summary)
        self.session.commit()

        rows = self.session.query(TranslationCache).filter_by(source_lang="zh", target_lang="en").all()
        self.assertEqual(len(rows), 1)
        vocab = self.session.query(Vocabulary).one()
        self.assertEqual(vocab.term, rows[0].term)
        self.assertEqual(vocab.cache_id, rows[0].id)


if __name__ == "__main__":
    unittest.main()
