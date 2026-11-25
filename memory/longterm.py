"""Long-term memory (MongoDB): customer history, past cases and policies.

* Customer history -> ``customer_history`` collection (via the repository)
* Past cases       -> ``past_cases`` collection
* Policies         -> ``policies`` collection, searched by ``retrieve_policy()``

``retrieve_policy(query, top_k, category)`` is the stable interface for the
agents.  The search strategy sits behind the ``PolicyRetriever`` interface:
today ``KeywordPolicyRetriever``; a Chroma/vector retriever can be added later
by subclassing ``PolicyRetriever`` and calling ``set_policy_retriever(...)``
without changing ``retrieve_policy``'s signature or return shape.
"""
from __future__ import annotations

import abc
import re
from typing import Any, Dict, List, Optional

from db import mongo
from db.models import build_past_case, build_policy, utc_now_iso
from db.repository import customer_history

# ==========================================================================
# Customer history (persistent)
# ==========================================================================
store_customer_history = customer_history.store_customer_history
get_customer_history = customer_history.get_customer_history
get_previous_returns = customer_history.get_previous_returns
count_previous_returns = customer_history.count_previous_returns


# ==========================================================================
# Past cases
# ==========================================================================
def store_past_case(case: Dict[str, Any]) -> Dict[str, Any]:
    """Store a resolved case (required: customer_id, issue_type, summary).

    Useful optional fields: run_id, decision, outcome, order_ids, tags.
    """
    return mongo.insert_document(mongo.PAST_CASES, build_past_case(case))


def get_past_case(case_id: str) -> Optional[Dict[str, Any]]:
    """Return one past case or ``None``."""
    return mongo.get_document(mongo.PAST_CASES, {"case_id": case_id})


def get_past_cases(customer_id: Optional[str] = None, issue_type: Optional[str] = None,
                   limit: int = 10) -> List[Dict[str, Any]]:
    """Return past cases, newest first, optionally filtered."""
    query: Dict[str, Any] = {}
    if customer_id:
        query["customer_id"] = customer_id
    if issue_type:
        query["issue_type"] = issue_type
    return mongo.find_documents(mongo.PAST_CASES, query, sort=[("created_at", -1)], limit=limit)


def update_past_case(case_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """Update fields of a past case (e.g. its outcome)."""
    return mongo.update_document(mongo.PAST_CASES, {"case_id": case_id}, updates)


def search_past_cases(query: str, customer_id: Optional[str] = None,
                      limit: int = 5) -> List[Dict[str, Any]]:
    """Keyword-search past cases (issue_type, summary, tags, decision, outcome)."""
    tokens = _tokenize(query)
    if not tokens:
        return []
    base: Dict[str, Any] = {"customer_id": customer_id} if customer_id else {}
    scored = []
    for case in mongo.find_documents(mongo.PAST_CASES, base, sort=[("created_at", -1)], limit=500):
        haystack = set(_tokenize(" ".join(
            [str(case.get(f) or "") for f in ("issue_type", "summary", "decision", "outcome")]
            + list(case.get("tags") or []))))
        score = len(tokens & haystack)
        if score:
            scored.append((score, case))
    scored.sort(key=lambda pair: pair[0], reverse=True)  # stable: keeps newest-first on ties
    return [{**case, "score": score} for score, case in scored[:limit]]


def get_relevant_history(customer_id: str, query: Optional[str] = None,
                         issue_type: Optional[str] = None, limit: int = 5) -> Dict[str, Any]:
    """Bundle the historical information agents typically need for a customer.

    Returns ``{"customer_id", "previous_returns_6m" (int), "recent_returns",
    "recent_activity", "past_cases"}``.  ``past_cases`` uses ``query`` (keyword
    match) when given, else ``issue_type``/recency.
    """
    if query:
        cases = search_past_cases(query, customer_id=customer_id, limit=limit)
    else:
        cases = get_past_cases(customer_id=customer_id, issue_type=issue_type, limit=limit)
    return {
        "customer_id": customer_id,
        "previous_returns_6m": count_previous_returns(customer_id, months=6),
        "recent_returns": get_previous_returns(customer_id, months=6),
        "recent_activity": get_customer_history(customer_id, limit=limit),
        "past_cases": cases,
    }


# ==========================================================================
# Policies
# ==========================================================================
def store_policy(policy: Dict[str, Any]) -> Dict[str, Any]:
    """Insert or replace a policy by ``policy_id`` (required: policy_id, title, content)."""
    doc = build_policy(policy)
    mongo.upsert_document(mongo.POLICIES, {"policy_id": doc["policy_id"]}, doc)
    return doc


def get_policy(policy_id: str) -> Optional[Dict[str, Any]]:
    """Return one policy or ``None``."""
    return mongo.get_document(mongo.POLICIES, {"policy_id": policy_id})


def list_policies(category: Optional[str] = None, active_only: bool = True) -> List[Dict[str, Any]]:
    """List policies, optionally by category / only active ones."""
    query: Dict[str, Any] = {}
    if category:
        query["category"] = category
    if active_only:
        query["active"] = True
    return mongo.find_documents(mongo.POLICIES, query, sort=[("policy_id", 1)])


def delete_policy(policy_id: str) -> bool:
    """Delete a policy; True if one was removed."""
    return mongo.delete_document(mongo.POLICIES, {"policy_id": policy_id})


_STOPWORDS = frozenset(
    "a an and are as at be by for from has have i in is it its my of on or that the this to "
    "was were will with me not no do does did can could should would".split())


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def _tokenize(text: str) -> set:
    """Lower-case word set with stopwords removed and trailing 's' stripped."""
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if w not in _STOPWORDS}


class PolicyRetriever(abc.ABC):
    """Interface for policy search back-ends (keyword now, vector later)."""

    @abc.abstractmethod
    def retrieve(self, query: str, top_k: int = 3,
                 category: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return up to ``top_k`` policy dicts, best first, each with a
        ``score`` (higher is better) and ``retrieval_method`` field."""


class KeywordPolicyRetriever(PolicyRetriever):
    """Keyword scoring over the MongoDB ``policies`` collection.

    Weights: keyword-list match 3, exact keyword phrase in query +2,
    title match 2, content match 1.
    """

    def retrieve(self, query: str, top_k: int = 3,
                 category: Optional[str] = None) -> List[Dict[str, Any]]:
        tokens = _tokenize(query)
        if not tokens:
            return []
        query_text = " ".join(re.findall(r"[a-z0-9]+", query.lower()))
        results = []
        for policy in list_policies(category=category, active_only=True):
            keyword_tokens: set = set()
            phrase_bonus = 0
            for phrase in policy.get("keywords", []):
                keyword_tokens |= _tokenize(phrase)
                if phrase and re.search(rf"\b{re.escape(phrase)}\b", query_text):
                    phrase_bonus += 2
            score = (3 * len(tokens & keyword_tokens) + phrase_bonus
                     + 2 * len(tokens & _tokenize(policy.get("title", "")))
                     + len(tokens & _tokenize(policy.get("content", ""))))
            if score > 0:
                results.append({**policy, "score": score, "retrieval_method": "keyword"})
        results.sort(key=lambda p: (-p["score"], p["policy_id"]))
        return results[:top_k]


_retriever: PolicyRetriever = KeywordPolicyRetriever()


def set_policy_retriever(retriever: PolicyRetriever) -> None:
    """Swap the search back-end (e.g. a future Chroma retriever)."""
    global _retriever
    if not isinstance(retriever, PolicyRetriever):
        raise TypeError("retriever must be a PolicyRetriever.")
    _retriever = retriever


def get_policy_retriever() -> PolicyRetriever:
    """Return the active policy retriever."""
    return _retriever


def retrieve_policy(query: str, top_k: int = 3,
                    category: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return the policies most relevant to ``query`` (stable agent interface).

    Each result is a policy document (policy_id, title, category, keywords,
    content, version, ...) plus ``score`` and ``retrieval_method``.  Returns
    ``[]`` when nothing matches.
    """
    return _retriever.retrieve(query, top_k=top_k, category=category)


if __name__ == "__main__":  # basic verification only (needs seeded MongoDB: python -m db.mongo)
    print("policy 'refund for failed order':",
          [(p["policy_id"], p["score"]) for p in retrieve_policy("refund for failed order payment")])
    print("relevant history:", get_relevant_history("CUST-1001")["previous_returns_6m"], "return(s) in 6 months")
