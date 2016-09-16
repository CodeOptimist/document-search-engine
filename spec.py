import re
from books import Books

from whoosh.fields import ID, TEXT, Schema, STORED
from whoosh import index, analysis


def clean(_text):
    text = _text.replace('\n', '').replace('*', '').replace('#', '').strip()
    text = re.sub(r' +', r' ', text).title()
    return text


def add_document(writer, d, session, content):
    d['session'] = session
    d['content'] = content
    d['part_title'] = ""
    if 'part' in d:
        title = re.sub(r'^Part (One|Two)\b\s*', r'', d['part'])
        if title:
            d['part_title'] = "- {}<br />".format(title)
    d['chapter_num'] = re.sub(r'^(Chapter \d+).*', r'\1', d['chapter'])
    d['chapter_title'] = re.sub(r'^Chapter \d+\s*', r'', d['chapter'])
    writer.add_document(**d)


def create_index(indexdir):
    schema = Schema(book_abbr=STORED(),
                    book_name=STORED(),
                    book_url=STORED(),
                    part_title=STORED(),
                    chapter_num=STORED(),
                    chapter_title=STORED(),
                    book=ID(stored=True),
                    part=TEXT(stored=True, analyzer=analysis.StandardAnalyzer(minsize=1, stoplist=None)),
                    chapter=TEXT(stored=True, analyzer=analysis.StandardAnalyzer(minsize=1, stoplist=None)),
                    session=TEXT(stored=True, analyzer=analysis.StandardAnalyzer(minsize=1, stoplist=None)),
                    content=TEXT(stored=True, analyzer=analysis.StemmingAnalyzer()))

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
        # parts = text.split(book['part_split'])[1:] if 'part_split' in book else [text]
        parts = book['part_id_re'].split(text)
        for _part_id, _part in zip(parts[1::2], parts[2::2]):
            part = _part_id + _part
            part_id = clean(_part_id)
            d['part'] = part_id

            chapters = book['chapter_id_re'].split(part)
            for _chapter_id, _chapter in zip(chapters[1::2], chapters[2::2]):
                chapter = _chapter_id + _chapter
                chapter_id = clean(_chapter_id)
                d['chapter'] = chapter_id

                if chapter_id == 'Appendix':
                    del d['part']
                elif chapter_id == 'About The Author':
                    break
                print(chapter_id)

                sessions = book['session_id_re'].split(chapter)
                continues_session = None
                if chapter_id != 'Appendix':
                    continues_session = bool(re.search(r'[a-z]', sessions[0])) and last_session_id
                    if continues_session:
                        add_document(writer, d, last_session_id, sessions[0])

                for idx, (_session_id, _session) in enumerate(zip(sessions[1::2], sessions[2::2])):
                    session = _session_id + _session
                    if idx == 0 and continues_session == False:
                        session = sessions[0] + session

                    session_id = clean(_session_id)
                    if session:
                        add_document(writer, d, session_id, session)
                    last_session_id = session_id
                    print(session_id)


    writer.commit()
    return ix

