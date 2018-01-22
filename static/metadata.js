/*
     Copyright 2018 Google Inc.

    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at

        https://www.apache.org/licenses/LICENSE-2.0

    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.
*/

/**
 * @fileoverview Implements the dynamic creative review functions in the UI.
 * Depends on jQuery (2.1.4 at time of development), jQuery UI, highlight.js,
 * bootstrap (3.3.4 at time of development), and the bootstrap glyphicons fonts.
 */

$(function() {

  /**
   * The snippet name currently rendered, or none to render the first one.
   * @type {?string}
   */
  var snippet;

  /**
   * The scroll position for the snippet preview.
   * @type {Array<number>}
   */
  var scroll = [0, 0];

  /**
   * Constant holding all asset states, their labels and icons.
   * @type {Object<string,Object>}
   */
  var STATES = {
      none: {
          name: 'no upload',
          icon: 'glyphicon-minus gray',
          help: 'will not be uploaded (not autodetected)',
          order: 0
      },
      upload: {
          name: 'upload',
          icon: 'glyphicon-circle-arrow-up green',
          help: 'will be uploaded',
          order: 1
      },
      self_upload: {
          name: 'manual upload',
          icon: 'glyphicon-circle-arrow-up yellow',
          help: 'too large, serve from own server/CDN and replace macro',
          order: 2
      },
      inline: {
          name: 'manual inline',
          icon: 'glyphicon-exclamation-sign orange',
          help: 'asset contains macros, might need to be inlined in snippet',
          order: 3
      },
      unsupported: {
          name: 'not supported',
          icon: 'glyphicon-minus red',
          help: 'mimetype not supported by DFP',
          order: 4
      }
  };

  /**
   * The jQuery object holding the snippet preview elements.
   * @const {Array<Object>}
   */
  var $snippets = $('#snippets');

  function setupAdvertisersAutocomplete() {
    var $input = $('#advertiser_id'),
        $spinner = $('#advertisers_spinner');
    $input.autocomplete({
      minLength: 3,
      search: function() {
        var val = $input.val();
        if (val.indexOf(' ') == -1 && parseInt(val) == +val) {
          return false;
        }
        $spinner.css('visibility', 'visible');
      },
      source: function(request, response) {
        var url = '/advertisers/' + x5.transform.network_code + '/' +
            encodeURIComponent($input.val()) + '/';
        $.ajax({
            dataType: 'json',
            url: url,
            headers: {
                'X-XSRF-Token': $('#xsrf_token').val()
            }
        })
        .always(function() { $spinner.css('visibility', 'hidden'); })
        .success(function(data) {
          if (!data.error) {
            response($.map(data.data, function(value, key) {
              return {label: value.name, value: value.id};
            }));
          }
        });
      }
    });
  }

  function showAssets(includedNames) {
    /**
     * Displays the assets table, mapping icons and descriptions to each asset
     * state. Also displays a legend for states used in the table.
     * @param {!Array.<string>} includedNames Asset names to display.
     */
    var counts = {},
        statusIcons = [],
        statusSizes = {},
        $assets = $('#assets tbody'),
        $legend = $('#legend');
    $assets.empty();
    $legend.empty();
    // count occurrences of each asset
    includedNames.map(function(name) {
      this[name] = typeof(this[name]) == 'undefined' ? 1 : this[name] + 1;
    }, counts);
    // output rows
    x5.assets.forEach(function(asset) {
      var name = asset.name,
          count = counts[name],
          status = 'none';
      // set status
      if (count) {
        if (asset.inlined)
          status = 'inline';
        else if (asset.over_limit)
          status = 'self_upload';
        else if (asset.unsupported)
          status = 'unsupported';
        else
          status = 'upload';
      }
      statusIcons.push(status);
      statusSizes[status] = typeof(statusSizes[status]) == 'undefined' ?
        asset.size :
        statusSizes[status] + asset.size;
      // create and append row
      var $row = $(
        '<tr class="' + (counts[name] ? 'snippet' : 'bundle') + '">' +
        '<td><span class="glyphicon ' +
        STATES[status].icon +
        '" aria-hidden="true"></span></td>' +
        '<td class="name" title="' + name + '">' + (
          status != 'inline' ?
          asset.basename :
          '<a href="#">' + asset.basename + '</a>'
        ) + '</td>' +
        '<td>' + asset.id + '</td>' +
        '<td>' + (asset.mimetype || 'unknown') + '</td>' +
        '<td>' + asset.size + '</td>' +
        '<td>' + (counts[name] ? counts[name] : '') + '</td>s</tr>'
      ).appendTo($assets);
      // download element on click if it needs to be inlined
      if (status == 'inline') {
        $('a', $row)
        .click(function() {
          $(this).attr({
            href: 'data:' + asset.mimetype + ';charset=utf-8,' +
              encodeURIComponent(asset.parsed_content),
            target: '_blank'
          });
        });
      }
    });
    // output legend
    var $legendSizes = $('<div class="col-md-12"></div>').appendTo($legend),
        $legendIcons = $('<div class="col-md-12"></div>').appendTo($legend);

    $.each(statusSizes, function(k, v) {
      if (k == 'none')
        return;
      $legendSizes.append(
        '<div class="legend"><span>' + STATES[k].name + '</span>' +
        Math.round(v / 1000) + ' kb</div>'
      );
    });

    statusIcons
        .sort(function(a, b) { return STATES[a].order - STATES[b].order; })
        .filter(function(item, pos, self) { return self.indexOf(item) == pos; })
        .forEach(function(icon_name) {
          $legendIcons.append(
              '<div class="legend"><span class="glyphicon ' +
              STATES[icon_name].icon +
              '"></span>' + STATES[icon_name].help + '</div>'
          );
        });
  }

  function showSnippet() {
    /**
     * Updates the UI to show text and assets of the selected snippet.
     */
    $('#converted pre').text(snippet.parsed_content);
    $('#original pre').text(snippet.content);
    PR.prettyPrint();
    showAssets(snippet.assets);
  }

  // tab scroll

  $('#tabs a').on('show.bs.tab', function(e) {
    var $old = $(
      '#' + e.relatedTarget.attributes['aria-controls'].value + ' pre'
    );
    scroll = [$old.scrollTop(), $('code', $old).scrollLeft()];
  }).on('shown.bs.tab', function(e) {
    var $new = $('#' + e.target.attributes['aria-controls'].value + ' pre');
    $new.scrollTop(scroll[0]);
    $('code', $new).scrollLeft(scroll[1]);
  });

  // snippet change

  $snippets.change(function() {
    $.each(x5.snippets, function(i, s) {
      if (s.name == $snippets.val())
        snippet = s;
    });
    showSnippet();
  });

  function bootstrap() {
    /**
     * Bootstraps the UI, calling all functions needed to display or activate
     * individual components.
     */
    // sort assets by type / name
    x5.assets.sort(function(a, b) {
      return +(a.id > b.id) || +(a.id === b.id) - 1;
    });
    snippet = x5.snippets[0];
    $.each(x5.snippets, function(i, s) {
      $snippets.append('<option>' + s.name + '</option>');
    });
    showSnippet();
    setupAdvertisersAutocomplete();
  }

  // TODO: check if it's possible to get here with no snippets
  bootstrap();

});
