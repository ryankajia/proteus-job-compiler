/* Proteus dashboard behaviour.
 *
 * Kept out of webapp.py on purpose: this used to live inside an f-string,
 * where every literal { and } had to be doubled. One missed brace and the
 * module would not parse — which has happened.
 *
 * Loaded on every page with `defer`, so each block guards on the elements it
 * needs and does nothing where they are absent.
 */

/* Drag-and-drop for the job-document upload box (/new). */
(function () {
  var zone = document.getElementById('dropzone');
  var input = document.getElementById('docsinput');
  var list = document.getElementById('pickedlist');
  if (!zone || !input) return;

  function show() {
    list.innerHTML = '';
    for (var i = 0; i < input.files.length; i++) {
      var li = document.createElement('li');
      var kb = input.files[i].size / 1024;
      li.textContent = input.files[i].name + ' (' +
        (kb > 1024 ? (kb / 1024).toFixed(1) + ' MB' : Math.round(kb) + ' KB') + ')';
      list.appendChild(li);
    }
  }

  // The whole page must swallow the default drop, or the browser navigates
  // away to the dropped file and the half-filled form is lost.
  ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(function (ev) {
    document.addEventListener(ev, function (e) { e.preventDefault(); });
    zone.addEventListener(ev, function (e) { e.preventDefault(); e.stopPropagation(); });
  });
  ['dragenter', 'dragover'].forEach(function (ev) {
    zone.addEventListener(ev, function () { zone.classList.add('over'); });
  });
  ['dragleave', 'drop'].forEach(function (ev) {
    zone.addEventListener(ev, function () { zone.classList.remove('over'); });
  });

  zone.addEventListener('drop', function (e) {
    var dropped = e.dataTransfer && e.dataTransfer.files;
    if (!dropped || !dropped.length) return;
    // Merge with anything already chosen instead of replacing it: a PD and a
    // separate ad text are often added in two goes.
    var dt = new DataTransfer();
    for (var i = 0; i < input.files.length; i++) dt.items.add(input.files[i]);
    for (var j = 0; j < dropped.length; j++) dt.items.add(dropped[j]);
    input.files = dt.files;
    show();
  });

  input.addEventListener('change', show);
})();

/* Interface language only. Job ads and application documents keep their source language. */
(function () {
  var button = document.getElementById('language-switch');
  if (!button) return;
  var pairs = [
    ['我的求职看板', 'My Job Dashboard'], ['今天先看这里', 'Today at a glance'],
    ['今天新增记录', 'Added today'], ['值得核对', 'Worth checking'],
    ['我想做', 'Interested'], ['已投递', 'Submitted'], ['已拒绝', 'Rejected'],
    ['优先看的岗位', 'Priority roles'], ['我想做的岗位', 'Roles I like'],
    ['投递记录与结果', 'Applications and outcomes'], ['被拒岗位类型', 'Rejected role types'],
    ['详细记录', 'Activity log'], ['根据招聘信息准备材料', 'Prepare documents from an ad'],
    ['AI 找岗位', 'AI job search'], ['+ 记录岗位', '+ Track a job'],
    ['找适合我的岗位', 'Find suitable roles'], ['这次想找什么岗位', 'What roles to search'],
    ['搜索现在的岗位', 'Search current roles'], ['最近一次搜索', 'Latest search'],
    ['返回看板', 'Back to dashboard'], ['还没有岗位。点击右上角“+ 记录岗位”开始。', 'No jobs yet. Use “+ Track a job” to begin.'],
    ['“今天新增记录”按进入本系统的日期计算。“值得核对”仅统计有链接、明确截止日期、初筛匹配且未过期的岗位，仍须打开雇主原文核对。已投递请以确认邮件为准.',
      '“Added today” uses the date saved in this system. “Worth checking” counts open roles with a link, known deadline and initial fit. Verify the employer page before applying. A submission counts when the confirmation email arrives.'],
    ['拒绝所用天数仅在投递和拒绝事件都有日期时显示；岗位类型样本很少时，不应只凭拒绝次数排除整个类别。',
      'Days to rejection are shown only when both dates are recorded. Small samples are not enough to rule out a whole role type.'],
    ['搜索会调用 MiniMax 的实时网页搜索。结果是线索，请打开雇主原文核对岗位、资格与截止日期；确认后点“+ 记录岗位”加入看板。',
      'Search uses MiniMax live web search. Treat results as leads: verify the employer page, eligibility and deadline, then track the role.'],
    ['还没有确认投递记录。', 'No confirmed applications recorded yet.'],
    ['目前没有已记录且符合初筛的开放岗位。', 'No open roles currently pass the initial screen.'],
    ['点岗位旁的 ☆ 想做，收藏你真正感兴趣的工作。', 'Select ☆ Interested beside a role to save it here.'],
    ['Sync master CV', 'Sync master CV'], ['To do', 'To do'],
    ['Docs ready — NOT SUBMITTED', 'Documents ready — not submitted'],
    ['Started on portal', 'Started on portal'], ['Submitted', 'Submitted'],
    ['In progress', 'In progress'], ['Offer 🎉', 'Offer 🎉'], ['Closed', 'Closed'],
    ['Closing within 7 days', 'Closing within 7 days'],
    ['Ready, NOT submitted', 'Ready, not submitted'],
    ['Submitted, awaiting', 'Submitted, awaiting reply'], ['Still open', 'Still open'],
    ['Date unknown', 'Closing date unknown']
  ];
  var enToZh = {
    'Sync master CV': '同步主简历', 'To do': '待处理',
    'Docs ready — NOT SUBMITTED': '材料已备好，尚未投递',
    'Started on portal': '已开始填写申请', 'Submitted': '已投递',
    'In progress': '面试进行中', 'Offer 🎉': '收到录用', 'Closed': '已结束',
    'Closing within 7 days': '7 天内截止',
    'Ready, NOT submitted': '材料已备好，尚未投递',
    'Submitted, awaiting': '已投递，等待回复', 'Still open': '仍在招聘',
    'Date unknown': '截止日期未知', 'Strong': '匹配较高',
    'Possible': '可以考虑', 'Stretch': '有一定差距',
    'Not scored': '未评分', 'Visa-restricted': '签证限制'
  };
  var zhToEn = {};
  pairs.forEach(function (pair) { zhToEn[pair[0]] = pair[1]; });
  Object.keys(enToZh).forEach(function (key) { zhToEn[enToZh[key]] = key; });
  var english = localStorage.getItem('proteus-language') === 'en';
  function translate() {
    document.documentElement.lang = english ? 'en' : 'zh-CN';
    button.textContent = english ? '中文' : 'English';
    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    var node;
    while ((node = walker.nextNode())) {
      var parent = node.parentElement;
      if (!parent || parent.closest('pre,code,textarea,.doc,.resume,script,style,#language-switch')) continue;
      var raw = node.nodeValue;
      var value = raw.trim();
      if (!value) continue;
      var replacement = english ? zhToEn[value] : enToZh[value];
      if (replacement) node.nodeValue = raw.replace(value, replacement);
    }
  }
  button.addEventListener('click', function () {
    english = !english;
    localStorage.setItem('proteus-language', english ? 'en' : 'zh');
    translate();
  });
  translate();
})();
