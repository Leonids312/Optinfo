import 'package:flutter/material.dart';
import 'package:flutter_math_fork/flutter_math.dart';

/// A source-preserving token: invalid or incomplete formulas remain readable.
@immutable
class ScientificSegment {
  final String text;
  final String source;
  final bool isMath;
  final bool display;
  const ScientificSegment(this.text,
      {String? source, this.isMath = false, this.display = false})
      : source = source ?? text;
}

bool _escaped(String text, int index) {
  var slashes = 0;
  for (var i = index - 1; i >= 0 && text[i] == r'\'; i--) {
    slashes++;
  }
  return slashes.isOdd;
}

String _unescapeCurrency(String text) {
  final result = StringBuffer();
  for (var i = 0; i < text.length; i++) {
    if (text[i] == r'\' &&
        !_escaped(text, i) &&
        i + 1 < text.length &&
        text[i + 1] == r'$') {
      result.write(r'$');
      i++;
    } else {
      result.write(text[i]);
    }
  }
  return result.toString();
}

/// Supports \(...\), \[...\], $...$ and $$...$$ without interpreting prose
/// as Markdown. A dollar followed by a price must not consume the next price.
List<ScientificSegment> parseScientificText(String text) {
  final result = <ScientificSegment>[];
  var plainStart = 0;
  var cursor = 0;
  while (cursor < text.length) {
    String? open;
    String? close;
    var display = false;
    if (!_escaped(text, cursor)) {
      if (text.startsWith(r'\(', cursor)) {
        open = r'\(';
        close = r'\)';
      } else if (text.startsWith(r'\[', cursor)) {
        open = r'\[';
        close = r'\]';
        display = true;
      } else if (text.startsWith(r'$$', cursor)) {
        open = close = r'$$';
        display = true;
      } else if (text[cursor] == r'$' &&
          cursor + 1 < text.length &&
          !RegExp(r'\s').hasMatch(text[cursor + 1])) {
        open = close = r'$';
      }
    }
    if (open == null || close == null) {
      cursor++;
      continue;
    }
    final bodyStart = cursor + open.length;
    var end = text.indexOf(close, bodyStart);
    while (end >= 0 && _escaped(text, end)) {
      end = text.indexOf(close, end + close.length);
    }
    if (open == r'$' && end >= 0) {
      final after = end + 1 < text.length ? text[end + 1] : '';
      final body = text.substring(bodyStart, end);
      // Inline dollars cannot cross paragraphs or close immediately before a
      // new numeric price, e.g. "$5–$10" or "$5 and $10".
      if (end == bodyStart ||
          RegExp(r'\s').hasMatch(text[end - 1]) ||
          RegExp(r'[0-9]').hasMatch(after) ||
          body.contains('\n') ||
          (RegExp(r'^[0-9]').hasMatch(body) &&
              (RegExp(r'[；。！？]').hasMatch(body) ||
                  (RegExp(r'^[0-9,.]+[,，]$').hasMatch(body) &&
                      RegExp(r'[A-Za-z\\]').hasMatch(after))))) {
        end = -1;
      }
    }
    if (end < 0 || text.substring(bodyStart, end).trim().isEmpty) {
      cursor += open.length;
      continue;
    }
    if (cursor > plainStart) {
      result.add(ScientificSegment(
          _unescapeCurrency(text.substring(plainStart, cursor))));
    }
    final finish = end + close.length;
    result.add(ScientificSegment(text.substring(bodyStart, end).trim(),
        source: text.substring(cursor, finish),
        isMath: true,
        display: display));
    cursor = plainStart = finish;
  }
  if (plainStart < text.length || result.isEmpty) {
    result
        .add(ScientificSegment(_unescapeCurrency(text.substring(plainStart))));
  }
  return result;
}

/// Chinese prose and TeX mathematics share the same surrounding text style.
/// Preview text keeps its line limit; full articles put display math on its own
/// row. Every equation can scroll horizontally when the viewport is narrow.
class ScientificText extends StatelessWidget {
  final String data;
  final TextStyle? style;
  final int? maxLines;
  final TextOverflow? overflow;
  final TextAlign? textAlign;
  const ScientificText(this.data,
      {super.key, this.style, this.maxLines, this.overflow, this.textAlign});

  @override
  Widget build(BuildContext context) {
    final segments = parseScientificText(data);
    if (!segments.any((segment) => segment.isMath)) {
      return Text(segments.map((segment) => segment.text).join(),
          style: style,
          maxLines: maxLines,
          overflow: overflow,
          textAlign: textAlign);
    }
    final effective = DefaultTextStyle.of(context).style.merge(style);
    final inline = <ScientificSegment>[];
    final children = <Widget>[];
    void flush() {
      if (inline.isEmpty) return;
      children.add(_paragraph(List.of(inline), effective));
      inline.clear();
    }

    for (final segment in segments) {
      if (segment.display && maxLines == null) {
        flush();
        children.add(Padding(
          padding: const EdgeInsets.symmetric(vertical: 10),
          child: MathText(segment.text,
              source: segment.source, style: effective, display: true),
        ));
      } else {
        inline.add(segment);
      }
    }
    flush();
    return children.length == 1
        ? children.single
        : Column(
            crossAxisAlignment: CrossAxisAlignment.stretch, children: children);
  }

  Widget _paragraph(List<ScientificSegment> segments, TextStyle effective) =>
      Text.rich(
        TextSpan(children: [
          for (final segment in segments)
            if (!segment.isMath)
              TextSpan(text: segment.text)
            else
              WidgetSpan(
                alignment: PlaceholderAlignment.middle,
                child: MathText(segment.text,
                    source: segment.source, style: effective, inline: true),
              ),
        ]),
        style: effective,
        maxLines: maxLines,
        overflow: overflow,
        textAlign: textAlign,
      );
}

class MathText extends StatelessWidget {
  final String expression;
  final String? source;
  final TextStyle? style;
  final bool display;
  final bool inline;
  const MathText(this.expression,
      {super.key,
      this.source,
      this.style,
      this.display = false,
      this.inline = false});

  @override
  Widget build(BuildContext context) {
    final effective = DefaultTextStyle.of(context).style.merge(style);
    final fontSize = effective.fontSize ?? 14;
    final original = source ?? expression;
    final scale = inline
        ? 1.0
        : MediaQuery.textScalerOf(context).scale(fontSize) / fontSize;
    Widget fallback() => Text(original,
        key: const ValueKey('math-source-fallback'), style: effective);
    final child = expression.length > 4096
        ? fallback()
        : Math.tex(expression,
            mathStyle: display ? MathStyle.display : MathStyle.text,
            textStyle: effective,
            textScaleFactor: scale,
            onErrorFallback: (_) => fallback());
    final equation = Semantics(
      label: '公式：$expression',
      excludeSemantics: true,
      child: Tooltip(
        message: original,
        child: SingleChildScrollView(
          key: const ValueKey('math-horizontal-scroll'),
          scrollDirection: Axis.horizontal,
          primary: false,
          child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 2), child: child),
        ),
      ),
    );
    // Flutter scales WidgetSpan children automatically. Applying MediaQuery
    // again inside Math would square the user's reading/OS scale factor.
    return inline ? MediaQuery.withNoTextScaling(child: equation) : equation;
  }
}
