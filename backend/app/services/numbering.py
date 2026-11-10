"""
Document number generation (PO, GRN, purchase returns, stock adjustments).

Numbers look like "<prefix>-0001" and are issued from a row-locked counter in
`document_sequences`, so two users saving at the same moment get different
numbers instead of one of them failing on the unique constraint. Because the
counter update is part of the caller's transaction, a rolled-back document
does not consume a number.
"""
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.models.numbering import DocumentSequence


async def _highest_existing_suffix(
    db: AsyncSession,
    number_column: InstrumentedAttribute,
    pharmacy_column: InstrumentedAttribute,
    pharmacy_id: int,
    prefix: str,
    separator: str = "-",
) -> int:
    """Highest numeric suffix already used for this prefix (documents created before the counter existed)."""
    stmt = (
        select(number_column)
        .where(pharmacy_column == pharmacy_id, number_column.like(f"{prefix}{separator}%"))
        # Longer suffixes are larger numbers ("-10000" > "-9999"), then compare lexically
        .order_by(func.length(number_column).desc(), number_column.desc())
        .limit(1)
    )
    latest = (await db.execute(stmt)).scalar_one_or_none()
    if not latest:
        return 0
    suffix = latest[len(prefix) + len(separator):]
    return int(suffix) if suffix.isdigit() else 0


async def _lock_sequence(db: AsyncSession, pharmacy_id: int, doc_type: str, period_key: str):
    stmt = (
        select(DocumentSequence)
        .where(
            DocumentSequence.pharmacy_id == pharmacy_id,
            DocumentSequence.doc_type == doc_type,
            DocumentSequence.period_key == period_key,
        )
        .with_for_update()
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def next_document_number(
    db: AsyncSession,
    pharmacy_id: int,
    doc_type: str,
    prefix: str,
    number_column: InstrumentedAttribute,
    pharmacy_column: InstrumentedAttribute,
    separator: str = "-",
    width: int = 4,
) -> str:
    """
    Issue the next number for `prefix` (e.g. "PO-202610" -> "PO-202610-0001",
    or with separator "/" and width 5: "INV/26-27" -> "INV/26-27/00001").

    `number_column` / `pharmacy_column` identify the document table so that the
    counter starts above any number already in use.
    """
    sequence = await _lock_sequence(db, pharmacy_id, doc_type, prefix)

    if sequence is None:
        start_from = await _highest_existing_suffix(
            db, number_column, pharmacy_column, pharmacy_id, prefix, separator
        )
        try:
            # Savepoint: if another request creates the same counter row first,
            # only this insert is rolled back, not the caller's transaction.
            async with db.begin_nested():
                sequence = DocumentSequence(
                    pharmacy_id=pharmacy_id,
                    doc_type=doc_type,
                    period_key=prefix,
                    last_value=start_from,
                )
                db.add(sequence)
                await db.flush()
        except IntegrityError:
            sequence = await _lock_sequence(db, pharmacy_id, doc_type, prefix)

    sequence.last_value += 1
    await db.flush()
    return f"{prefix}{separator}{sequence.last_value:0{width}d}"
