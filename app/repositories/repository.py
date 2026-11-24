"""
Repository layer — thin DB-access abstraction over SQLAlchemy async sessions.
"""

from typing import Optional, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.models.admission import AdmissionCycle, Course, CutoffMark, University
from app.models.chat import Conversation, Message, MessageFeedback
from app.models.knowledge import DocumentChunk, KnowledgeDocument
from app.models.user import RefreshToken, User, UserProfile

# ---------------------------------------------------------------------------
# UserRepository
# ---------------------------------------------------------------------------


class UserRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, user_id: int) -> Optional[User]:
        result = await self.db.execute(
            select(User).where(User.id == user_id).options(joinedload(User.profile))
        )
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> Optional[User]:
        result = await self.db.execute(
            select(User).where(User.email == email).options(joinedload(User.profile))
        )
        return result.scalar_one_or_none()

    async def get_by_username(self, username: str) -> Optional[User]:
        result = await self.db.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()

    async def create(self, user: User) -> User:
        self.db.add(user)
        await self.db.flush()  # flush needed: caller may use user.id immediately
        return user

    async def update_last_login(self, user_id: int) -> None:
        await self.db.execute(
            update(User).where(User.id == user_id).values(last_login_at=func.now())
        )

    async def save_refresh_token(self, token: RefreshToken) -> RefreshToken:
        self.db.add(token)
        await self.db.flush()  # flush needed: token.id may be used by caller
        return token

    async def get_refresh_token(self, token_hash: str) -> Optional[RefreshToken]:
        result = await self.db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
        )
        return result.scalar_one_or_none()

    async def revoke_refresh_token(self, token_hash: str) -> None:
        await self.db.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == token_hash)
            .values(revoked=True, revoked_at=func.now())
        )

    async def upsert_profile(self, profile: UserProfile) -> UserProfile:
        self.db.add(profile)
        await self.db.flush()  # send SQL, get any DB-generated fields
        await self.db.commit()
        await self.db.refresh(profile)
        return profile


# ---------------------------------------------------------------------------
# AdmissionRepository
# ---------------------------------------------------------------------------


class AdmissionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Universities ────────────────────────────────────────────────────────

    async def list_universities(
        self,
        state: Optional[str] = None,
        university_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        name: Optional[str] = None,
    ) -> Sequence[University]:
        q = select(University).where(University.is_active.is_(True))
        if name:
            q = q.where(
                University.name.ilike(f"%{name}%") | University.abbreviation.ilike(f"%{name}%")
            )
        if state:
            q = q.where(University.state.ilike(f"%{state}%"))
        if university_type:
            q = q.where(University.university_type == university_type)
        q = q.order_by(University.name, University.id).limit(limit).offset(offset)
        result = await self.db.execute(q)
        return result.scalars().all()

    async def get_university_by_id(self, university_id: int) -> Optional[University]:
        result = await self.db.execute(
            select(University)
            .where(University.id == university_id, University.is_active.is_(True))
            .options(selectinload(University.courses))
        )
        return result.scalar_one_or_none()

    async def search_universities(
        self,
        name: str,
        limit: int = 20,
        offset: int = 0,
    ) -> Sequence[University]:
        # FIX: accept limit/offset so pagination is honoured for name searches too
        result = await self.db.execute(
            select(University)
            .where(
                (University.name.ilike(f"%{name}%") | University.abbreviation.ilike(f"%{name}%"))
            )
            .where(University.is_active.is_(True))  # FIX: idiomatic boolean check
            .order_by(University.name, University.id)
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().all()

    # ── Courses ─────────────────────────────────────────────────────────────

    async def get_courses_by_name(
        self, course_name: str, university_id: Optional[int] = None
    ) -> Sequence[Course]:
        q = (
            select(Course)
            .where(Course.name.ilike(f"%{course_name}%"))
            .where(Course.is_active.is_(True))
            .options(joinedload(Course.university))
        )
        if university_id:
            q = q.where(Course.university_id == university_id)
        result = await self.db.execute(q)
        return result.scalars().all()

    async def get_courses_by_university(self, university_id: int) -> Sequence[Course]:
        # FIX: removed joinedload(Course.university) — redundant when filtering by university_id
        result = await self.db.execute(
            select(Course)
            .where(Course.university_id == university_id)
            .where(Course.is_active.is_(True))
        )
        return result.scalars().all()

    # ── CutoffMarks ─────────────────────────────────────────────────────────

    async def get_cutoffs(
        self,
        cycle_id: int,
        university_id: Optional[int] = None,
        course_id: Optional[int] = None,
    ) -> Sequence[CutoffMark]:
        # FIX: use selectinload instead of multiple joinedloads to avoid
        # multiplying JOIN cardinality across university + course + cycle
        q = (
            select(CutoffMark)
            .where(CutoffMark.cycle_id == cycle_id)
            .options(
                selectinload(CutoffMark.university),
                selectinload(CutoffMark.course),
                selectinload(CutoffMark.cycle),
            )
        )
        if university_id:
            q = q.where(CutoffMark.university_id == university_id)
        if course_id:
            q = q.where(CutoffMark.course_id == course_id)
        result = await self.db.execute(q)
        return result.scalars().all()

    async def get_universities_by_score(
        self, course_name: str, jamb_score: int, cycle_id: int
    ) -> Sequence[CutoffMark]:
        # FIX: corrected docstring — cutoff <= student score means student qualifies
        """Return cutoffs where the required cutoff score <= student's score (eligible universities)."""
        result = await self.db.execute(
            select(CutoffMark)
            .join(CutoffMark.course)
            .where(CutoffMark.cycle_id == cycle_id)
            .where(CutoffMark.score <= jamb_score)
            .where(Course.name.ilike(f"%{course_name}%"))
            .options(
                selectinload(CutoffMark.university),
                selectinload(CutoffMark.course),
            )
            .order_by(CutoffMark.score.desc())
        )
        return result.scalars().all()

    # ── AdmissionCycle ──────────────────────────────────────────────────────

    async def get_current_cycle(self) -> Optional[AdmissionCycle]:
        result = await self.db.execute(
            select(AdmissionCycle).where(AdmissionCycle.is_current.is_(True))
        )
        return result.scalar_one_or_none()

    async def get_cycle_by_year(self, year: int) -> Optional[AdmissionCycle]:
        result = await self.db.execute(select(AdmissionCycle).where(AdmissionCycle.year == year))
        return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# ChatRepository
# ---------------------------------------------------------------------------


class ChatRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # FIX: shared filter logic extracted — ownership + deletion check in one place
    def _base_conversation_query(self, conversation_id: int, user_id: int):
        return (
            select(Conversation)
            .where(Conversation.id == conversation_id)
            .where(Conversation.user_id == user_id)
            .where(Conversation.status != "deleted")
        )

    async def create_conversation(self, conv: Conversation) -> Conversation:
        self.db.add(conv)
        await self.db.flush()  # flush needed: conv.id used by callers
        return conv

    async def get_conversation(self, conversation_id: int, user_id: int) -> Optional[Conversation]:
        result = await self.db.execute(self._base_conversation_query(conversation_id, user_id))
        return result.scalar_one_or_none()

    async def get_conversation_with_messages(
        self, conversation_id: int, user_id: int
    ) -> Optional[Conversation]:
        result = await self.db.execute(
            self._base_conversation_query(conversation_id, user_id).options(
                selectinload(Conversation.messages)
            )
        )
        return result.scalar_one_or_none()

    async def list_conversations(
        self, user_id: int, limit: int = 20, offset: int = 0
    ) -> Sequence[Conversation]:
        result = await self.db.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .where(Conversation.status == "active")
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().all()

    async def latest_student_context(self, user_id: int, exclude_id: Optional[int] = None) -> dict:
        """Working memory of the student's most recent other conversation (episodic recall)."""
        q = (
            select(Conversation.student_context)
            .where(Conversation.user_id == user_id)
            .where(Conversation.status == "active")
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
            .limit(1)
        )
        if exclude_id:
            q = q.where(Conversation.id != exclude_id)
        return (await self.db.scalar(q)) or {}

    async def create_message(self, message: Message) -> Message:
        self.db.add(message)
        await self.db.flush()  # flush needed: message.id used by callers
        return message

    async def get_recent_messages(self, conversation_id: int, limit: int = 20) -> Sequence[Message]:
        # DESC + Python reverse: gets the N most recent then returns them oldest-first.
        # This is correct and efficient (avoids a subquery). The reversal is intentional.
        result = await self.db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .where(Message.state == "completed")
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )
        return list(reversed(result.scalars().all()))

    async def update_conversation_stats(
        self,
        conversation_id: int,
        token_delta: int,
        message_delta: int = 2,  # FIX: parameterised — default 2 (user + assistant)
    ) -> None:
        await self.db.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(
                message_count=Conversation.message_count + message_delta,
                total_tokens=Conversation.total_tokens + token_delta,
                last_message_at=func.now(),
            )
        )

    async def save_feedback(self, feedback: MessageFeedback) -> MessageFeedback:
        self.db.add(feedback)
        await self.db.flush()
        return feedback

    async def update_conversation_title(self, conversation_id: int, title: str) -> None:
        await self.db.execute(
            update(Conversation).where(Conversation.id == conversation_id).values(title=title)
        )

    async def soft_delete_conversation(self, conversation_id: int, user_id: int) -> None:
        await self.db.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .where(Conversation.user_id == user_id)
            .values(status="deleted", deleted_at=func.now())
        )

    async def get_message_for_user(self, message_id: int, user_id: int) -> Optional[Message]:
        """Return a message only if it belongs to a conversation owned by user_id."""
        result = await self.db.execute(
            select(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(Message.id == message_id)
            .where(Conversation.user_id == user_id)
            .where(Conversation.status != "deleted")
        )
        return result.scalar_one_or_none()

    async def get_feedback(self, message_id: int, user_id: int) -> Optional[MessageFeedback]:
        result = await self.db.execute(
            select(MessageFeedback)
            .where(MessageFeedback.message_id == message_id)
            .where(MessageFeedback.user_id == user_id)
        )
        return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# KnowledgeRepository
# ---------------------------------------------------------------------------


class KnowledgeRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_document(self, doc: KnowledgeDocument) -> KnowledgeDocument:
        self.db.add(doc)
        await self.db.flush()  # flush needed: doc.id used by callers
        return doc

    async def get_document(self, doc_id: int) -> Optional[KnowledgeDocument]:
        result = await self.db.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.id == doc_id)
        )
        return result.scalar_one_or_none()

    async def list_documents(
        self,
        category: Optional[str] = None,
        cycle_year: Optional[int] = None,
        limit: int = 50,  # FIX: added pagination — no more full-table scans
        offset: int = 0,
    ) -> Sequence[KnowledgeDocument]:
        q = select(KnowledgeDocument).where(KnowledgeDocument.is_active.is_(True))
        if category:
            q = q.where(KnowledgeDocument.category == category)
        if cycle_year:
            q = q.where(
                (KnowledgeDocument.cycle_year == cycle_year)
                | (KnowledgeDocument.cycle_year.is_(None))
            )
        result = await self.db.execute(
            q.order_by(KnowledgeDocument.created_at.desc()).limit(limit).offset(offset)
        )
        return result.scalars().all()

    async def add_chunk(self, chunk: DocumentChunk) -> DocumentChunk:
        self.db.add(chunk)
        # No flush — chunk.id rarely needed immediately; let session batch
        return chunk

    async def get_chunks_by_document(self, doc_id: int) -> Sequence[DocumentChunk]:
        result = await self.db.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == doc_id)
            .order_by(DocumentChunk.chunk_index)
        )
        return result.scalars().all()

    async def update_document_status(
        self, doc_id: int, status: str, chunk_count: Optional[int] = None
    ) -> None:
        # FIX: use `is not None` so chunk_count=0 is explicitly settable
        values: dict = {"status": status}
        if chunk_count is not None:
            values["chunk_count"] = chunk_count
        if status == "indexed":
            values["indexed_at"] = func.now()
        await self.db.execute(
            update(KnowledgeDocument).where(KnowledgeDocument.id == doc_id).values(**values)
        )
