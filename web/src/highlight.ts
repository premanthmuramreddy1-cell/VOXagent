/** Small local syntax highlighter: comments, strings, numbers, keywords and calls for common languages. */

const KEYWORDS: Record<string, string> = {
  js: 'async await break case catch class const continue default delete do else export extends false finally for from function if import in instanceof let new null return static super switch this throw true try typeof undefined var void while yield interface type enum implements readonly as',
  py: 'and as assert async await break class continue def del elif else except False finally for from global if import in is lambda None nonlocal not or pass raise return self True try while with yield',
  c: 'auto break case char class const continue default delete do double else enum extern false float for friend goto if inline int long namespace new nullptr private protected public return short signed sizeof static struct switch template this throw true try typedef typename union unsigned using virtual void volatile while include define bool',
  java: 'abstract boolean break byte case catch char class const continue default do double else enum extends false final finally float for if implements import instanceof int interface long new null package private protected public return short static super switch this throw throws true try void while var record fun val when object',
  go: 'break case chan const continue default defer else fallthrough false for func go goto if import interface map nil package range return select struct switch true type var',
  rs: 'as async await break const continue crate else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true type unsafe use where while',
  rb: 'begin break case class def defined do else elsif end ensure false for if in module next nil not or redo rescue retry return self super then true undef unless until when while yield',
  sh: 'if then else elif fi for while do done case esac function in return export local echo',
  sql: 'select from where insert into values update set delete create table drop alter join left right inner outer on group by order having limit and or not null primary key',
}

const FAMILY: Record<string, string> = {
  js: 'js', jsx: 'js', ts: 'js', tsx: 'js', mjs: 'js', cjs: 'js', json: 'js',
  py: 'py', c: 'c', h: 'c', cpp: 'c', hpp: 'c', cc: 'c', cs: 'java',
  java: 'java', kt: 'java', swift: 'java', dart: 'java', php: 'java', go: 'go', rs: 'rs', rb: 'rb',
  sh: 'sh', bash: 'sh', zsh: 'sh', sql: 'sql',
}

export function languageOf(path: string): string {
  const ext = path.split('.').pop()?.toLowerCase() ?? ''
  return FAMILY[ext] ?? ''
}

const escapeHtml = (text: string) => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

export function highlight(code: string, path: string): string {
  const family = languageOf(path)
  if (!family) return escapeHtml(code)
  const words = new Set(KEYWORDS[family].split(' '))
  const hashComments = family === 'py' || family === 'sh' || family === 'rb'
  const lineComment = hashComments ? '#' : family === 'sql' ? '--' : '//'
  const parts = [
    family === 'py' ? `"""[\\s\\S]*?"""|'''[\\s\\S]*?'''` : '',
    hashComments ? '' : '/\\*[\\s\\S]*?\\*/',
    `${lineComment.replace(/[-/]/g, '\\$&')}[^\\n]*`,
    '"(?:[^"\\\\\\n]|\\\\.)*"', "'(?:[^'\\\\\\n]|\\\\.)*'", '`(?:[^`\\\\]|\\\\.)*`',
    '\\b\\d[\\d_]*(?:\\.\\d+)?(?:[eE][+-]?\\d+)?\\b', '[A-Za-z_$][\\w$]*',
  ].filter(Boolean)
  const pattern = new RegExp(parts.join('|'), 'g')
  let out = ''
  let last = 0
  for (const match of code.matchAll(pattern)) {
    const token = match[0]
    const index = match.index ?? 0
    out += escapeHtml(code.slice(last, index))
    last = index + token.length
    const first = token[0]
    let cls = ''
    if (token.startsWith('"""') || token.startsWith("'''")) cls = 'tok-str'
    else if (token.startsWith(lineComment) || token.startsWith('/*')) cls = 'tok-com'
    else if (first === '"' || first === "'" || first === '`') cls = 'tok-str'
    else if (/\d/.test(first)) cls = 'tok-num'
    else if (words.has(family === 'sql' ? token.toLowerCase() : token)) cls = 'tok-kw'
    else if (code[last] === '(') cls = 'tok-fn'
    out += cls ? `<span class="${cls}">${escapeHtml(token)}</span>` : escapeHtml(token)
  }
  return out + escapeHtml(code.slice(last))
}
