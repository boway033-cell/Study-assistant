from uuid import uuid4


def test_library_sorting_and_subset_reorder_are_persistent():
    from fastapi.testclient import TestClient
    from sqlalchemy import delete, func, select

    from backend.app.core.database import SessionLocal
    from backend.app.main import app
    from backend.app.models import Book, PaperProfile, shelf_books

    marker = uuid4().hex
    db = SessionLocal()
    created_ids: list[int] = []
    try:
        baseline_total = db.scalar(select(func.count(Book.id))) or 0
        baseline_reading = db.scalar(select(func.count(PaperProfile.book_id)).where(PaperProfile.reading_status == "reading")) or 0
        baseline_favorite = db.scalar(select(func.count(PaperProfile.book_id)).where(PaperProfile.favorite == 1)) or 0
        baseline_unfiled = db.scalar(select(func.count(Book.id)).where(Book.id.not_in(select(shelf_books.c.book_id)))) or 0
        alpha = Book(
            title=f"Alpha {marker}", file_path=f"alpha-{marker}.pdf", file_type="pdf",
            status="ready", library_order=900_001,
        )
        beta = Book(
            title=f"Beta {marker}", file_path=f"beta-{marker}.pdf", file_type="pdf",
            status="ready", library_order=900_002,
        )
        gamma = Book(
            title=f"Gamma {marker}", file_path=f"gamma-{marker}.pdf", file_type="pdf",
            status="ready", library_order=900_003,
        )
        db.add_all([alpha, beta, gamma])
        db.flush()
        created_ids = [alpha.id, beta.id, gamma.id]
        db.add_all([
            PaperProfile(book_id=alpha.id, authors="Chen", published_year=2020, reading_status="reading"),
            PaperProfile(book_id=beta.id, authors="Bao", published_year=2024, favorite=True),
            PaperProfile(book_id=gamma.id, authors="Deng", published_year=2022),
        ])
        db.commit()

        client = TestClient(app)
        by_year = client.get("/api/books", params=[
            ("ids", alpha.id), ("ids", beta.id), ("ids", gamma.id),
            ("sort_by", "year_desc"), ("page_size", 10),
        ])
        assert by_year.status_code == 200
        assert [item["id"] for item in by_year.json()["items"]] == [beta.id, gamma.id, alpha.id]

        filtered = client.get("/api/books", params={"ids": alpha.id, "page_size": 10})
        assert filtered.status_code == 200
        assert filtered.json()["total"] == 1
        # 左侧智能视图必须始终是全库统计，不能被当前 ids / 书架 / 状态筛选覆盖。
        stats = filtered.json()["stats"]
        assert stats["total"] == baseline_total + 3
        assert stats["reading"] == baseline_reading + 1
        assert stats["favorite"] == baseline_favorite + 1
        assert stats["unfiled"] == baseline_unfiled + 3

        reordered = client.put("/api/books/order", json={
            "book_ids": [gamma.id, alpha.id, beta.id], "shelf_id": None,
        })
        assert reordered.status_code == 200
        custom = client.get("/api/books", params=[
            ("ids", alpha.id), ("ids", beta.id), ("ids", gamma.id),
            ("sort_by", "custom"), ("page_size", 10),
        ])
        assert custom.status_code == 200
        assert [item["id"] for item in custom.json()["items"]] == [gamma.id, alpha.id, beta.id]
    finally:
        if created_ids:
            db.execute(delete(PaperProfile).where(PaperProfile.book_id.in_(created_ids)))
            db.execute(delete(Book).where(Book.id.in_(created_ids)))
            db.commit()
        db.close()


def test_shelf_reorder_does_not_change_global_order():
    from fastapi.testclient import TestClient
    from sqlalchemy import delete, insert, select

    from backend.app.core.database import SessionLocal
    from backend.app.main import app
    from backend.app.models import Book, Shelf, shelf_books

    marker = uuid4().hex
    db = SessionLocal()
    shelf = Shelf(name=f"sort-{marker}", order_index=999_999)
    books = [
        Book(title=f"One {marker}", file_path=f"one-{marker}.pdf", file_type="pdf", status="ready", library_order=910_001),
        Book(title=f"Two {marker}", file_path=f"two-{marker}.pdf", file_type="pdf", status="ready", library_order=910_002),
        Book(title=f"Three {marker}", file_path=f"three-{marker}.pdf", file_type="pdf", status="ready", library_order=910_003),
    ]
    try:
        db.add(shelf)
        db.add_all(books)
        db.flush()
        for index, book in enumerate(books):
            db.execute(insert(shelf_books).values(shelf_id=shelf.id, book_id=book.id, order_index=index))
        db.commit()
        global_before = [book.library_order for book in books]

        response = TestClient(app).put("/api/books/order", json={
            "book_ids": [books[2].id, books[0].id, books[1].id], "shelf_id": shelf.id,
        })
        assert response.status_code == 200
        shelf_order = list(db.scalars(
            select(shelf_books.c.book_id).where(shelf_books.c.shelf_id == shelf.id)
            .order_by(shelf_books.c.order_index)
        ).all())
        assert shelf_order == [books[2].id, books[0].id, books[1].id]
        db.expire_all()
        assert [db.get(Book, book.id).library_order for book in books] == global_before
    finally:
        db.execute(delete(shelf_books).where(shelf_books.c.shelf_id == shelf.id))
        for book in books:
            if book.id:
                stored = db.get(Book, book.id)
                if stored:
                    db.delete(stored)
        stored_shelf = db.get(Shelf, shelf.id) if shelf.id else None
        if stored_shelf:
            db.delete(stored_shelf)
        db.commit()
        db.close()
