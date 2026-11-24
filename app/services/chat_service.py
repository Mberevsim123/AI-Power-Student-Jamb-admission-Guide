"""Admissions conversation orchestration and durable turn state."""

import asyncio
import logging
import re
import time
from dataclasses import asdict, dataclass
from typing import Optional

from app.agent.core import AdmissionsAgent
from app.agent.formatter import tidy
from app.agent.links import evidence_hosts, guard_links
from app.agent.verifier import audit_tables, disclosure, verify
from app.core.config import settings
from app.core.exceptions import ConversationNotFoundError, LLMQuotaError, LLMUnavailableError
from app.models.chat import Conversation, Message, QueryIntent
from app.services.local_knowledge import NO_EVIDENCE, baseline_context, render_local_answer

logger = logging.getLogger(__name__)


def _needs_prior_context(message: str) -> bool:
    """Recognize explicit references whose evidence depends on the preceding turn."""
    return bool(
        re.search(r"\b(?:its|it|that school|that university|that course|there)\b", message, re.I)
    )


@dataclass
class Turn:
    conv: Conversation
    assistant: Message
    prompt: list
    fallback: Optional[str]
    first_message: str
    started: float
    local_answer: str
    board: object = None
    profile: object = None
    evidence: Optional[dict] = None


class ChatService:
    def __init__(
        self, db, chat_repo, adm_service, intent_clf, retriever, prompt_bld, llm, agent=None
    ):
        self.db, self.chat_repo, self.adm_service = db, chat_repo, adm_service
        self.intent_clf, self.retriever, self.prompt_bld, self.llm = (
            intent_clf,
            retriever,
            prompt_bld,
            llm,
        )
        self.agent = agent or AdmissionsAgent(db, adm_service)
        self.agent_event = None

    async def _prepare_turn(self, user, user_message, conversation_id):
        started = time.monotonic()
        if conversation_id:
            conv = await self.chat_repo.get_conversation(conversation_id, user.id)
            if conv is None:
                raise ConversationNotFoundError(conversation_id)
        else:
            conv = await self.chat_repo.create_conversation(
                Conversation(user_id=user.id, student_context={})
            )
        # Fetch history before saving this turn: the latest user message appears once.
        history = list(
            await self.chat_repo.get_recent_messages(conv.id, limit=settings.CHAT_HISTORY_MESSAGES)
        )
        intent = await self.intent_clf.classify(user_message)
        previous = next(
            (m for m in reversed(history) if m.role == "user" and m.state == "completed"), None
        )
        query = user_message
        if (
            intent == QueryIntent.out_of_scope
            and previous
            and re.search(
                r"\b(and|what about|that|it|there|those|also|yes|no|why|how)\b|^\s*20\d{2}[?\s]*$",
                user_message,
                re.I,
            )
        ):
            query = previous.content + "\n" + user_message
            intent = await self.intent_clf.classify(query)
        elif (
            previous
            and intent not in (QueryIntent.greeting, QueryIntent.out_of_scope)
            and previous.intent not in (QueryIntent.greeting.value, QueryIntent.out_of_scope.value)
            and _needs_prior_context(user_message)
        ):
            # Keep the current intent, but supply the antecedent to evidence retrieval.
            query = previous.content + "\n" + user_message
        # Agent: perceive, revise beliefs, let specialists plan and act on verified tools.
        recall = None
        if not history and intent in (QueryIntent.greeting, QueryIntent.out_of_scope):
            recall = await self._recall(user.id, conv.id)
        board = await self.agent.deliberate(
            user=user, conv=conv, message=user_message, intent=intent, query=query, recall=recall
        )
        intent, query = board.intent, board.query
        user_msg = Message(
            conversation_id=conv.id,
            user_id=user.id,
            role="user",
            content=user_message,
            state="completed",
            intent=intent.value,
        )
        await self.chat_repo.create_message(user_msg)
        structured = await self._build_structured_context(intent, query, user.profile)
        structured.update(baseline_context(query, intent))
        years = re.findall(r"\b20\d{2}\b", query)
        sources = await self.retriever.retrieve(
            query=query,
            intent=intent,
            top_k=settings.RAG_TOP_K,
            cycle_year=int(years[-1]) if years else None,
        )
        # The rolling summary matters once older turns have left the history window.
        long_chat = (getattr(conv, "message_count", 0) or 0) >= settings.CHAT_HISTORY_MESSAGES
        prompt = self.prompt_bld.build(
            student_profile=user.profile,
            conversation_summary=conv.summary if long_chat else None,
            history=history,
            user_message=user_message,
            rag_sources=sources,
            structured_context=structured,
            agent_brief=self.agent.brief(board),
        )
        local_answer = render_local_answer(intent, query, structured, sources)
        addendum = self.agent.addendum(board)
        if addendum and intent not in (QueryIntent.greeting, QueryIntent.out_of_scope):
            # The agent's own analysis answers the turn; drop the generic "no evidence" line.
            local_answer = local_answer.replace("\n\n" + NO_EVIDENCE, "") + "\n\n" + addendum
        opener = self.agent.opener(board)
        if opener:
            banner, _, body = local_answer.partition("\n\n")
            local_answer = f"{banner}\n\n{opener}\n\n{body}"
        fallback = None
        if board.local_reply:
            fallback = board.local_reply  # social or safety reply: no model needed
        elif intent == QueryIntent.out_of_scope:
            name = f", {board.name}" if board.name else ""
            fallback = (
                f"That one's outside what I can help with{name} — I focus on JAMB and Nigerian "
                "university admissions. I can check your eligibility, compare cut-off marks, plan "
                "your subject combination, or walk you through registration, Post-UTME and CAPS."
            )
        elif not self.llm.providers or (
            not structured and not sources and not board.observations and not self.llm.can_search
        ):
            fallback = local_answer
        if fallback == local_answer:
            structured["answer_mode"] = "local_knowledge"
        evidence = [s.to_dict() | {"origin": "knowledge_base"} for s in sources]
        for value in structured.values():
            for row in value if isinstance(value, list) else [value]:
                if isinstance(row, dict) and row.get("source_url"):
                    if not any(s.get("source_url") == row["source_url"] for s in evidence):
                        evidence.append(
                            {
                                "doc_title": row.get("title")
                                or row.get("university")
                                or row.get("name")
                                or "Admissions record",
                                "source_url": row["source_url"],
                                "origin": "knowledge_base",
                                "cycle_year": row.get("year") or row.get("requirements_year"),
                            }
                        )
        self.agent_event = self.agent.event(board)
        assistant = await self.chat_repo.create_message(
            Message(
                conversation_id=conv.id,
                user_id=user.id,
                role="assistant",
                state="streaming",
                intent=intent.value,
                rag_sources=evidence,
                structured_context=structured | {"agent": self.agent_event},
            )
        )
        self.conversation_id, self.message_id = conv.id, assistant.id
        # Everything the model was given, plus the student's own words: the verifier's
        # reference for figures. Earlier assistant answers are excluded on purpose.
        grounding = {
            "structured": structured,
            "documents": [s.snippet for s in sources],
            "tools": board.observations,
            "student": [user_message]
            + [m.content for m in history if m.role == "user" and m.content],
            "known": board.beliefs.known(),
            "alerts": [asdict(n) for n in board.nudges],  # verified calendar windows
        }
        return Turn(
            conv,
            assistant,
            prompt,
            fallback,
            user_message,
            started,
            local_answer,
            board,
            user.profile,
            grounding,
        )

    async def _recall(self, user_id, conversation_id):
        """Long-term episodic memory: what the student focused on last time."""
        recent = getattr(self.chat_repo, "latest_student_context", None)
        if recent is None:
            return None
        memory = await recent(user_id, exclude_id=conversation_id) or {}
        facts, focus = memory.get("facts", {}), memory.get("focus", {})
        course = focus.get("course") or facts.get("course", {}).get("value")
        universities = focus.get("universities") or facts.get("universities", {}).get("value")
        return {"course": course, "universities": universities or []} if course else None

    def _review(self, ctx, text):
        """Quality gate for model answers: tidy Markdown, then check it against evidence.

        Looping rows are removed, tables of unverifiable figures are replaced by a
        notice, and remaining unsupported figures, timings or institution names are
        disclosed. Nothing reaches the student before this runs.
        """
        web_cited = bool(self.llm.web_sources)
        text, removed = audit_tables(tidy(text), ctx.evidence, web_cited=web_cited)
        hosts = evidence_hosts(ctx.evidence, self.llm.web_sources, ctx.assistant.rag_sources)
        text, unlinked = guard_links(text, hosts)
        result = verify(text, ctx.evidence, web_cited=web_cited) | {
            "tables_removed": removed,
            "links_removed": unlinked,
        }
        agent = dict(ctx.assistant.structured_context.get("agent") or {})
        agent["verification"] = result
        ctx.assistant.structured_context = ctx.assistant.structured_context | {"agent": agent}
        flagged = len(result["unsupported"]) + len(result["timing"]) + len(result["identity"])
        if flagged or removed or unlinked:
            logger.info("Review flagged %d claim(s), removed %d table(s)", flagged, len(removed))
        return text + disclosure(result)

    def _use_local(self, ctx):
        ctx.fallback = ctx.local_answer
        ctx.assistant.model_used = "local-guidance"
        ctx.assistant.structured_context = ctx.assistant.structured_context | {
            "answer_mode": "local_knowledge"
        }
        ctx.assistant.error_message = None
        ctx.assistant.token_count = 0
        ctx.assistant.state = "completed"
        ctx.assistant.content = ctx.local_answer

    def _capture_evidence(self, ctx):
        if self.llm.web_sources:
            ctx.assistant.rag_sources = ctx.assistant.rag_sources + self.llm.web_sources
        if self.llm.web_grounding:
            ctx.assistant.structured_context = ctx.assistant.structured_context | {
                "web_grounding": self.llm.web_grounding
            }

    async def _finish(self, ctx):
        ctx.assistant.latency_ms = int((time.monotonic() - ctx.started) * 1000)
        if ctx.board is not None and ctx.assistant.state == "completed":
            self.agent.reflect(ctx.board, ctx.conv, ctx.profile)
        await self.db.flush()
        await self.chat_repo.update_conversation_stats(
            ctx.conv.id, token_delta=ctx.assistant.token_count or 0
        )
        if ctx.conv.message_count <= 2:
            await self.chat_repo.update_conversation_title(ctx.conv.id, ctx.first_message[:60])
        await self.db.commit()

    async def chat(self, user, user_message, conversation_id=None):
        ctx = await self._prepare_turn(user, user_message, conversation_id)
        try:
            if ctx.fallback:
                text, tokens = ctx.fallback, 0
            else:
                text, tokens = await self.llm.complete(ctx.prompt)
                self._capture_evidence(ctx)
                text = self._review(ctx, text)
            ctx.assistant.content = text
            ctx.assistant.token_count = tokens
            ctx.assistant.model_used = (
                "local-guidance"
                if ctx.fallback
                else (self.llm.model_used or settings.model_for(settings.LLM_PROVIDER))
            )
            ctx.assistant.state = "completed"
        except (LLMQuotaError, LLMUnavailableError) as exc:
            logger.warning(
                "All AI providers unavailable (%s); using local evidence", type(exc).__name__
            )
            self._use_local(ctx)
        except Exception as exc:
            logger.exception("Generation failed")
            ctx.assistant.state = "failed"
            ctx.assistant.error_message = "AI service unavailable"
            raise LLMUnavailableError() from exc
        finally:
            await self._finish(ctx)
        return ctx.assistant

    async def chat_stream(self, user, user_message, conversation_id=None):
        ctx = await self._prepare_turn(user, user_message, conversation_id)
        text = ""
        # A dict frame carries the agent's plan before any answer text arrives.
        yield {"agent": self.agent_event}
        try:
            if ctx.fallback:
                text = ctx.fallback
                yield text
            else:
                # Providers deliver a complete answer (failover never leaks partial
                # text), so reviewing before display adds no latency.
                async for chunk in self.llm.stream(ctx.prompt):
                    text += chunk
                text = self.llm.final_text or text
                self._capture_evidence(ctx)
                text = self._review(ctx, text)
                yield text
            ctx.assistant.content = text
            ctx.assistant.token_count = (
                0
                if ctx.fallback
                else self.llm.usage_tokens
                if self.llm.usage_tokens is not None
                else int(len(text.split()) * 1.3)
            )
            ctx.assistant.model_used = (
                "local-guidance"
                if ctx.fallback
                else (self.llm.model_used or settings.model_for(settings.LLM_PROVIDER))
            )
            ctx.assistant.state = "completed"
        except (LLMQuotaError, LLMUnavailableError) as exc:
            logger.warning(
                "All AI providers unavailable (%s); using local evidence", type(exc).__name__
            )
            self._use_local(ctx)
            text = ctx.local_answer
            yield text
        except (asyncio.CancelledError, GeneratorExit):
            ctx.assistant.state = "failed"
            ctx.assistant.error_message = "Stream disconnected"
            raise
        except Exception as exc:
            logger.exception("Streaming generation failed")
            ctx.assistant.state = "failed"
            ctx.assistant.error_message = "AI service unavailable"
            raise LLMUnavailableError() from exc
        finally:
            if ctx.assistant.state == "failed":
                ctx.assistant.content = text or None
            await self._finish(ctx)

    async def _build_structured_context(self, intent, message, profile):
        if intent in (QueryIntent.greeting, QueryIntent.out_of_scope):
            return {}
        # Match known names in the message; profile preferences are a fallback.
        from sqlalchemy import select

        from app.models.admission import Course, University

        courses = list(
            (await self.db.scalars(select(Course).where(Course.is_active.is_(True)))).all()
        )
        universities = list(
            (await self.db.scalars(select(University).where(University.is_active.is_(True)))).all()
        )
        from app.services.local_knowledge import match_university, normalize

        text = normalize(message)
        course = next(
            (
                c.name
                for c in sorted(courses, key=lambda c: len(c.name), reverse=True)
                if f" {normalize(c.name)} " in f" {text} "
            ),
            None,
        )
        university = next(
            (
                u.name
                for u in universities
                if f" {normalize(u.name)} " in f" {text} "
                or (
                    u.abbreviation
                    and re.search(r"\b" + re.escape(u.abbreviation) + r"\b", message, re.I)
                )
            ),
            None,
        )
        matched_directory = match_university(message)
        if not university and len(matched_directory) == 1:
            university = matched_directory[0]["name"]
        if (
            not university
            and intent != QueryIntent.school_recommendation
            and re.search(r"\b(university|polytechnic|college)\b", message, re.I)
        ):
            # An unrecognised named school must not turn into an all-schools query.
            return {}
        course = course or (profile.preferred_course if profile else None)
        years = re.findall(r"\b20\d{2}\b", message)
        year = int(years[-1]) if years else None
        cycle = (
            await self.adm_service.repo.get_cycle_by_year(year)
            if year
            else await self.adm_service.get_current_cycle()
        )
        ctx = {}
        if intent in (
            QueryIntent.cutoff_lookup,
            QueryIntent.school_recommendation,
            QueryIntent.course_eligibility,
        ) and (course or university):
            data = await self.adm_service.get_cutoffs(
                university_name=university, course_name=course, year=year
            )
            # Unofficial or unsourced scores cannot support an admissions claim.
            from app.core.gemini_client import safe_web_url

            data = [
                row
                for row in data
                if row.get("is_official") and safe_web_url(row.get("source_url"))
            ]
            if data:
                ctx["cutoffs"] = data[:10]
        if (
            intent
            in (
                QueryIntent.subject_combination,
                QueryIntent.olevel_requirement,
                QueryIntent.course_eligibility,
            )
            and course
        ):
            data = await self.adm_service.get_subject_combination(course, university)
            data = [
                d
                for d in data
                if d.get("source_url") and cycle and d.get("requirements_year") == cycle.year
            ]
            if data:
                ctx["subject_combinations"] = data
        if intent == QueryIntent.post_utme_info and university:
            data = await self.adm_service.get_post_utme(university, year)
            if data:
                ctx["post_utme"] = data
        if intent == QueryIntent.jamb_process:
            if cycle and cycle.cycle_notes.get("source_url"):
                ctx["registration"] = {
                    k: getattr(cycle, k)
                    for k in (
                        "year",
                        "registration_start",
                        "registration_end",
                        "exam_start",
                        "exam_end",
                    )
                }
                ctx["registration"].update(cycle.cycle_notes)
        return ctx
