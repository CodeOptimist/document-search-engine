function filterBook(abbr) {
    var abbr_l = abbr.toLowerCase();
    var q = document.getElementById('query').value;

    var book_re = new RegExp("\\bbook:" + abbr_l + "\\b\\??");
    var not_book_re = new RegExp("\\bNOT\\s+book:" + abbr_l + "\\b\\??");
    var any_book_re = new RegExp("\\bbook:\\w+\\??");

    if (not_book_re.test(q)) {
        new_q = q.replace(not_book_re, "book:" + abbr_l)
    } else if (book_re.test(q)) {
        new_q = q.replace(book_re, "NOT book:" + abbr_l)
    } else if (any_book_re.test(q)) {
        new_q = q.replace(any_book_re, "book:" + abbr_l);
    } else {
        new_q = (q + " book:" + abbr_l + " ").trimLeft();
    }
    document.getElementById('query').value = new_q;
    document.getElementById('query').focus();
}

function toggleDisplay(self, id) {
    e = document.getElementById(id);
    if (e.style.display === 'none') {
        e.style.removeProperty('display');
        self.innerText = '▼';
    } else {
        e.style.display = 'none';
        self.innerText = '►';
    }
}

function findAncestor(el, cls) {
    while ((el = el.parentElement) && !el.classList.contains(cls)) {
    }
    return el;
}

function addCitation(event) {
    if (!event.clipboardData)
        return;

    var selection = window.getSelection();
    var excerpts = findAncestor(selection.anchorNode, "excerpts");
    if (!excerpts)
        return;

    event.preventDefault();
    var heading = excerpts.parentNode.childNodes[3].innerHTML;

    var copyText = selection.toString().trim().replace(/ {2,}/g, ' ');
    if (copyText.indexOf("\n\n") === -1) {
        copyText = '"' + copyText + '"';
    } else {
        if (gReadableLayout)
            copyText = '"' + copyText + '"\n';
        else
            copyText = '• "' + copyText.replace(/(\n{2,})/g, '"$1• "') + '"\n';
    }
    copyText += "\n—" + heading;
    event.clipboardData.setData('Text', copyText);
}

document.addEventListener('copy', addCitation);

function fadeInputs(name, opacity) {
    inputs = document.getElementsByTagName("input");
    for (i = 0; i < inputs.length; i++) {
        var input = inputs[i];
        if (input.name === name)
            input.style.opacity = opacity;
    }
}

if (gHitOrder) {
    document.getElementById('explicit-hit-order').checked = true;
} else {
    fadeInputs('hit-order', 0.5);
}

if (gExcerptOrder) {
    document.getElementById('explicit-excerpt-order').checked = true;
} else {
    fadeInputs('excerpt-order', 0.5);
}

function hitOrder(radio) {
    fadeInputs('hit-order', 1.0);
    document.getElementById('explicit-hit-order').checked = true;
}

function excerptOrder(radio) {
    fadeInputs('excerpt-order', 1.0);
    document.getElementById('explicit-excerpt-order').checked = true;
}

(function () {
    var elem;
    if (location.hash) {
        if (history && history.replaceState) {
            elem = document.getElementById(location.hash.replace('#', ''));
            if (!elem) elem = document.getElementById(gScrollTo);
            if (elem)
                elem.scrollIntoView();
            history.replaceState(null, null, '.');
        }
    } else {
        elem = document.getElementById(gScrollTo);
        if (elem)
            elem.scrollIntoView();
    }
})();