from whoosh.analysis import RegexTokenizer, LowercaseFilter, StopFilter, STOP_WORDS, default_pattern, Filter, StemFilter, stem
from whoosh.highlight import Fragmenter, Fragment, BasicFragmentScorer
from whoosh.scoring import BM25F
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import re


def get_sentence_fragments(paragraph):
    paragraph_soup = BeautifulSoup(paragraph, 'lxml')

    result = []
    sentence_split = filter(None, re.split(r'(.*?(?:\.”|(?<!\b\w)(?<!\b(?:Dr|Sr|Jr|Mr|Ms))(?<!\bMrs)\.|[?!])[\s$])', paragraph))
    last_match_idx = None
    raw_sentence = ''
    for s_idx, raw_sentence in enumerate(sentence_split):
        raw_sentence = raw_sentence.strip('\n')

        if 'class="match ' not in raw_sentence:
            if s_idx == 0:
                result.append('')
            continue

        sentence_soup = BeautifulSoup(raw_sentence, 'lxml')
        deepest_sentence_tag = get_deepest_tag(sentence_soup, paragraph_soup)
        is_italics = deepest_sentence_tag.name == 'em' or any(tag.name == 'em' for tag in deepest_sentence_tag.parents)

        sentence = str(sentence_soup.body)
        sentence = re.sub(r'^<body>|</body>$', r'', sentence)
        sentence = re.sub(r'^<p>|</p>$', r'', sentence)
        sentence = re.sub(r'^<li>|</li>$', r'', sentence)

        if is_italics and not sentence.startswith('<em>'):
            sentence = '<em>{}</em>'.format(sentence)

        is_adjacent = s_idx - 1 == last_match_idx
        if is_adjacent:
            result[-1] += sentence
        else:
            result.append(sentence)
        last_match_idx = s_idx
    if 'class="match ' not in raw_sentence:
        result.append('')
    return result


def get_deepest_tag(needle_soup, haystack_soup):
    punctuation_ends_re = r'(^\W+|\W+$)'
    needle_strings = re.sub(punctuation_ends_re, '', ''.join(needle_soup.strings))

    result = None
    for tag in haystack_soup.find_all(True):
        tag_strings = re.sub(punctuation_ends_re, '', ''.join(tag.strings))
        if needle_strings in tag_strings:
            result = tag

    assert result
    return result


class ParagraphFragmenter(Fragmenter):
    def fragment_matches(self, text, matched_tokens):
        pass

    def fragment_tokens(self, text, tokens):
        paragraph_tokens = []
        last = (None, None)

        for t in tokens:
            if t.matched:
                cur = self.get_paragraph_pos(text, t)

                if cur != last and paragraph_tokens:
                    yield Fragment(text, paragraph_tokens, last[0], last[1])
                    paragraph_tokens = []
                paragraph_tokens.append(t.copy())
                last = cur

        if paragraph_tokens:
            yield Fragment(text, paragraph_tokens, last[0], last[1])

    @staticmethod
    def get_paragraph_pos(text, t):
        try:
            paragraph_start = text[:t.startchar].rindex('\n')
        except ValueError:
            paragraph_start = 0
        try:
            paragraph_end = t.endchar + text[t.endchar:].index('\n')
        except ValueError:
            paragraph_end = len(text)
        return paragraph_start, paragraph_end


class ConsistentFragmentScorer(BasicFragmentScorer):
    def __call__(self, f):
        score = super(ConsistentFragmentScorer, self).__call__(f)

        # fragment_text = f.text[f.startchar:f.endchar]
        if f.startchar:
            score += 1 / f.startchar
        return score


class DateBM25F(BM25F):
    use_final = True
    def final(self, searcher, docnum, score):
        fields = searcher.stored_fields(docnum)
        score = 1 - 1 / score
        if 'date' in fields:
            chapter_m = re.search(r'chapter\W*(\d+)', fields['heading'], re.IGNORECASE)
            chapter = int(chapter_m.group(1)) if chapter_m else 0
            date = fields['date'] + timedelta(seconds=chapter)
            assert isinstance(date, datetime)
            if isinstance(self, DescDateBM25F):
                date_score = (date - datetime(1800, 1, 1)).total_seconds()
            elif isinstance(self, AscDateBM25F):
                date_score = (datetime(2200, 1, 1) - date).total_seconds()
            else:
                raise NotImplementedError
            score += date_score + 1.0
            score /= 10**9
        return score


class DescDateBM25F(DateBM25F):
    pass


class AscDateBM25F(DateBM25F):
    pass


def CleanupStandardAnalyzer(expression=default_pattern, stoplist=STOP_WORDS, minsize=2, maxsize=None, gaps=False):
    ret = RegexTokenizer(expression=expression, gaps=gaps)
    # added CleanupFilter here
    chain = ret | CleanupFilter() | LowercaseFilter()
    if stoplist is not None:
        chain = chain | StopFilter(stoplist=stoplist, minsize=minsize, maxsize=maxsize)
    return chain


def CleanupStemmingAnalyzer(expression=default_pattern, stoplist=STOP_WORDS,
                     minsize=2, maxsize=None, gaps=False, stemfn=stem,
                     ignore=None, cachesize=50000):

    ret = RegexTokenizer(expression=expression, gaps=gaps)
    # added CleanupFilter here
    chain = ret | CleanupFilter() | LowercaseFilter()
    if stoplist is not None:
        chain = chain | StopFilter(stoplist=stoplist, minsize=minsize, maxsize=maxsize)
    return chain | StemFilter(stemfn=stemfn, ignore=ignore, cachesize=cachesize)


class CleanupFilter(Filter):
    def __call__(self, tokens):
        for t in tokens:
            t.text = t.text.replace(r'*', '')
            yield t
