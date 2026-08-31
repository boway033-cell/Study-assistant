from uuid import uuid4


def test_library_sorting_and_subset_reorder_are_persistent():
    from fastapi.testclient import TestClient
    from sqlalchemy import delete

    from backend.app.core.database import SessionLocal
    from backend.app.main import app
    from backend.app.models import Book, PaperProfile

    marker = uuid4().hex
    db = SessionLocal()
    created_ids: list[int] = []
    try:
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
            PaperProfile(book_id=alpha.id, authors="Chen", published_year=2020),
            PaperProfile(book_id=beta.id, authors="Bao", published_year=2024),
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
