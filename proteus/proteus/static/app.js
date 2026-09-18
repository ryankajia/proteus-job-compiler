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
