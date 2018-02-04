# Copyright (c) 2018 Christopher Galpin
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.


# this file is not licensed under https://github.com/CodeOptimist/whoosh-galpin/blob/master/LICENSE
# it's MIT licensed (given above) for folding into Whoosh proper
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
