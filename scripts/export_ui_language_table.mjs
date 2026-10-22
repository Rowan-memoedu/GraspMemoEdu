// The review table is generated from production catalogs, never a second hand-maintained dictionary.
import fs from 'node:fs/promises';
import {staticMessages} from '../web/static-messages.js';
import {contextMessages} from '../web/context-library-messages.js';
import {readerMessages} from '../web/reader-messages.js';
import {portalMessages} from '../web/portal-messages.js';
import {errorMessages} from '../web/i18n.js';
import {trainingMessages} from '../web/training-messages.js';
import {paperMessages} from '../web/review-paper-messages.js';
const sections=[['导航、账号及公共界面',staticMessages],['学习页面',readerMessages],['课程、任务与设置',portalMessages],['题库训练',trainingMessages],['组卷复习',paperMessages],['语境阅读与音频',contextMessages],['操作与服务提示',errorMessages]];
const count=sections.reduce((n,[,catalog])=>n+Object.keys(catalog).length,0);
const escape=value=>String(value).replace(/\|/g,'\\|').replace(/\r?\n/g,'<br>');
const lines=['# 完整界面中英对照表','','本表从当前界面词库自动生成。修改以词库为准，随后重新生成并检查本表。术语约定见 [界面语言规范](ui-language.md)。', '',`共 ${count} 条界面文案映射（包括不同位置重复使用的文案）。花括号表示运行时填入的名称、数量或时间，不是页面上的固定文字。`, '', '不包括作者课程正文、题目、答案、解析或用户输入。首页的学科双语副标题按约定保留。'];
for(const [title,catalog] of sections){
 lines.push('',`## ${title}`,'','| English | 中文 |','|---|---|');
 for(const pair of Object.values(catalog))lines.push(`| ${escape(pair[1])} | ${escape(pair[0])} |`);
}
const output=lines.join('\n')+'\n';
if(process.argv.includes('--check')){
 const stored=await fs.readFile(new URL('../docs/ui-language-table.md',import.meta.url),'utf8');
 if(stored.replace(/\r\n/g,'\n')!==output)throw new Error('The bilingual table is stale; regenerate it from the catalogs.');
 console.log(`PASS bilingual table matches all ${count} production mappings`);
}else process.stdout.write(output);
