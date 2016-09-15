import re
from books import Books

from whoosh.fields import ID, TEXT, NUMERIC, Schema
from whoosh import index, analysis


def add_part_info(book, d, _part):
    part = book['part_split'] + _part
    part_num = book['part_num_re'].search(_part).group(1)
    part_num = part_num.replace('ONE', '1').replace('TWO', '2').replace('THREE', '3').replace('FOUR', '4')
    d['part_num'] = part_num

    part_title = None
    if 'part_title_re' in book:
        part_title = book['part_title_re'].search(_part).group(1)
        part_title = part_title.replace('\n', '').replace('*', '')
        part_title = re.sub(r' +', r' ', part_title).title()
    d['part_title'] = part_title
    print("Part", d['part_num'], d['part_title'])
    return part


def add_chapter_info(book, d, _chapter):
    chapter = book['chapter_split'] + _chapter
    chapter_id = book['chapter_id_re'].search(_chapter).group(1)
    chapter_id = ("Chapter " if chapter_id.isdigit() else "Session ") + chapter_id.title()
    d['chapter_id'] = chapter_id
    chapter_title = book['chapter_title_re'].search(_chapter).group(1)
    chapter_title = chapter_title.replace('\n', '').replace('*', '')
    chapter_title = re.sub(r' +', r' ', chapter_title).title()
    d['chapter_title'] = chapter_title
    print(d['chapter_id'], d['chapter_title'])
    return chapter


def create_index(indexdir):
    schema = Schema(book_name=ID(stored=True), book_url=ID(stored=True),
                    part_title=ID(stored=True), chapter_title=ID(stored=True),
                    book=ID(stored=True), book_abbr=ID(stored=True), part_num=NUMERIC(stored=True),
                    chapter_id=ID(stored=True), session_id=ID(stored=True),
                    session=TEXT(stored=True, analyzer=analysis.StemmingAnalyzer()))

    ix = index.create_in(indexdir, schema)
    writer = ix.writer()

    for book in Books.indexed:
        with open("books/{}.txt".format(book['abbr']), encoding='utf-8') as f:
            text = f.read()

        d = {
            'book_name': book['name'],
            'book_abbr': book['abbr'],
            'book_url': book['url'],
            'book': book['abbr'].lower(),
        }

        last_session_id = None
        parts = text.split(book['part_split'])[1:] if 'part_split' in book else [text]
        for part in parts:
            if 'part_split' in book:
                part = add_part_info(book, d, part)
            chapters = part.split(book['chapter_split'])
            for chapter in chapters[1:]:
                chapter = add_chapter_info(book, d, chapter)

                sessions = book['session_id_re'].split(chapter)
                top_section = sessions[0]
                m = re.findall(r'\n[\s\n]*', top_section)
                continues_session = len(m) > 3 and last_session_id
                if continues_session:
                    d['session_id'] = last_session_id
                    d['session'] = top_section
                    # writer.add_document(**d, session_id=last_session_id, session=top_section)
                    writer.add_document(**d)

                for idx, (session_id, _session) in enumerate(zip(sessions[1::2], sessions[2::2])):
                    session = session_id + _session
                    if idx == 0 and not continues_session:
                        session = top_section + session

                    session_id = session_id.title()
                    d['session_id'] = session_id
                    d['session'] = session
                    # writer.add_document(**d, session_id=session_id, session=session)

                    if session:
                        writer.add_document(**d)
                    last_session_id = session_id
                    print(session_id)


    writer.commit()
    return ix

