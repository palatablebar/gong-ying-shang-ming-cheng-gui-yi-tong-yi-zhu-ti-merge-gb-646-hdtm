// 页面动作：请求封装、按钮动作、复核判定、归一试算。
(function () {
  'use strict';

  var banner = document.getElementById('banner');
  var busy = false;

  function showError(text) {
    if (!banner) { return; }
    banner.textContent = text;
    banner.hidden = false;
  }

  function clearError() {
    if (banner) { banner.hidden = true; banner.textContent = ''; }
  }

  function describe(payload, status) {
    var code = payload && payload.reason_code ? payload.reason_code : String(status);
    var reason = payload && payload.reason ? payload.reason : '请求失败';
    return code + ' ' + reason;
  }

  function api(url, options) {
    return fetch(url, options).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (payload) {
        if (!response.ok) { throw new Error(describe(payload, response.status)); }
        return payload;
      });
    });
  }

  function run(button, task) {
    if (busy) { return; }
    busy = true;
    clearError();
    if (button) { button.disabled = true; }
    task().catch(function (error) {
      showError(error.message);
    }).then(function () {
      busy = false;
      if (button) { button.disabled = false; }
    });
  }

  function postJson(url, body) {
    return api(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    });
  }

  document.addEventListener('click', function (event) {
    var print = event.target.closest('[data-print]');
    if (print) { window.print(); return; }

    var action = event.target.closest('[data-action]');
    if (action) {
      var name = action.getAttribute('data-action');
      run(action, function () {
        if (name === 'rebuild-clusters') {
          return postJson('/api/clusters', {}).then(function () { window.location.reload(); });
        }
        if (name === 'reimport') {
          return postJson('/api/suppliers/import', { force: true })
            .then(function () { window.location.reload(); });
        }
        return Promise.resolve();
      });
      return;
    }

    var decision = event.target.closest('[data-decision]');
    if (decision) {
      var pairId = decision.getAttribute('data-pair');
      var version = parseInt(decision.getAttribute('data-version'), 10);
      run(decision, function () {
        return postJson('/api/reviews/' + encodeURIComponent(pairId) + '/decision', {
          decision: decision.getAttribute('data-decision'),
          expected_version: version,
          expected_impact: decision.getAttribute('data-impact-hash') || ''
        }).then(function () { window.location.reload(); });
      });
      return;
    }

    var impact = event.target.closest('[data-impact]');
    if (impact) {
      var impactPair = impact.getAttribute('data-impact');
      var row = document.querySelector('[data-impact-row="' + impactPair + '"]');
      var cell = document.querySelector('[data-impact-cell="' + impactPair + '"]');
      if (row && !row.hidden) { row.hidden = true; return; }
      run(impact, function () {
        return api('/api/reviews/' + encodeURIComponent(impactPair) + '/impact')
          .then(function (payload) {
            if (cell) { cell.innerHTML = renderImpact(payload); }
            if (row) { row.hidden = false; }
          });
      });
    }
  });

  function moneyText(value) {
    var number = Number(value);
    return isNaN(number) ? String(value) : number.toLocaleString('zh-CN', {
      minimumFractionDigits: 2, maximumFractionDigits: 2
    });
  }

  function memberLine(member) {
    return '<li><span class="mono">' + member.supplier_id + '</span> ' + member.raw_name +
      ' <span class="sub mono">' + (member.tax_id || '无税号') + ' · ' +
      moneyText(member.ytd_amount) + ' 元 · ' + member.source_file + ':' + member.line_no +
      '</span></li>';
  }

  function clusterView(title, view) {
    if (!view) { return ''; }
    var html = '<div class="impact-box"><h3>' + title + '：' + view.member_count +
      ' 成员 · ' + moneyText(view.total_amount) + ' 元</h3>' +
      '<div class="sub">税号：' + (view.taxes.join('、') || '无') +
      (view.conflict ? ' <span class="tag tag-bad">多税号</span>' : '') + '</div><ul>';
    view.members_detail = view.members_detail || [];
    return html + view.members_detail.map(memberLine).join('') + '</ul></div>';
  }

  function renderImpact(payload) {
    var merge = payload.merge || {};
    var html = '<div class="impact-grid">';
    if (merge.cluster) {
      html += clusterView('确认合并后同一付款对象', merge.cluster);
    } else {
      html += '<div class="impact-box"><h3>合并不生效</h3><div class="sub">' +
        (merge.block_code || '') + (merge.block_pairs && merge.block_pairs.length ?
        ' · 受约束对：' + merge.block_pairs.join('、') : '') + '</div></div>';
    }
    html += clusterView('左侧当前分量', merge.left_cluster) +
      clusterView('右侧当前分量', merge.right_cluster);
    html += '</div>';
    if (merge.evidence && merge.evidence.length) {
      html += '<div class="sub">既有证据边：' + merge.evidence.map(function (edge) {
        return edge.pair_id + '(' + edge.role + ' ' + edge.score + ')';
      }).join('、') + '</div>';
    }
    html += '<div class="btn-cell impact-actions">';
    if (merge.cluster) {
      html += '<button type="button" class="btn btn-sm" data-decision="merge" data-pair="' +
        payload.pair_id + '" data-version="' + payload.decision.version +
        '" data-impact-hash="' + (merge.impact_hash || '') + '">确认合并（按此影响）</button>';
    } else {
      html += '<span class="sub">合并当前不生效：先判定受约束对，再重新预览。</span>';
    }
    html += '<button type="button" class="btn btn-sm btn-ghost" data-decision="keep" data-pair="' +
      payload.pair_id + '" data-version="' + payload.decision.version +
      '" data-impact-hash="' + (payload.keep && payload.keep.impact_hash || '') +
      '">确认保留</button></div>';
    return html;
  }

  var previewForm = document.querySelector('[data-preview-form]');
  if (previewForm) {
    previewForm.addEventListener('submit', function (event) {
      event.preventDefault();
      var input = previewForm.querySelector('input[name="name"]');
      var output = previewForm.querySelector('[data-preview-output]');
      run(previewForm.querySelector('button'), function () {
        return postJson('/api/normalize/preview', { name: input.value })
          .then(function (payload) {
            output.textContent = payload.normalized + ' · ' + (payload.codes.join(' ') || '无规则命中');
          });
      });
    });
  }
})();
