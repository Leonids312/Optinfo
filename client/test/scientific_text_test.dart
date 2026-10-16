import 'package:flutter/material.dart';
import 'package:flutter_math_fork/flutter_math.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:optinfo/scientific_text.dart';

Future<void> mountMath(WidgetTester tester, Widget child,
    {double scale = 1, double width = 320}) async {
  await tester.pumpWidget(MaterialApp(
    home: Scaffold(
      body: MediaQuery(
        data: MediaQueryData(textScaler: TextScaler.linear(scale)),
        child: Align(
          alignment: Alignment.topLeft,
          child: SizedBox(width: width, child: child),
        ),
      ),
    ),
  ));
  await tester.pumpAndSettle();
}

void main() {
  test('parses all four delimiters and preserves Chinese prose', () {
    final parts = parseScientificText(
        r'载流子 $n_e$ 与 \(T_e\)，结果 $$E=mc^2$$ 以及 \[\frac{a}{b}\]。');
    expect(parts.where((p) => p.isMath).map((p) => p.text),
        [r'n_e', r'T_e', r'E=mc^2', r'\frac{a}{b}']);
    expect(parts.where((p) => p.display).length, 2);
    expect(parts.first.text, '载流子 ');
    expect(parts.last.text, '。');
    expect(parts.where((p) => p.isMath).first.source, r'$n_e$');
  });

  test('ordinary prices and escaped dollars stay prose', () {
    for (final value in [r'价格 $5–$10。', r'$5 and $10', r'价格 $125。']) {
      expect(parseScientificText(value).where((p) => p.isMath), isEmpty);
    }
    final parts = parseScientificText(r'成本 \$5；另有公式 $E=mc^2$。');
    expect(parts.first.text, r'成本 $5；另有公式 ');
    expect(parts.where((p) => p.isMath).single.text, 'E=mc^2');
    for (final text in [r'价格 $5,$x$ 是公式。', r'价格 $5；公式$x$。']) {
      expect(parseScientificText(text).where((p) => p.isMath).single.text, 'x');
    }
  });

  test('unclosed delimiters and empty equations stay readable', () {
    for (final value in [r'中文 \(x', r'价格 $5', r'空 \(\)', r'不完整 \[a']) {
      final parts = parseScientificText(value);
      expect(parts.where((p) => p.isMath), isEmpty);
      expect(parts.map((p) => p.text).join(), value);
    }
  });

  testWidgets('plain content keeps ordinary Text behavior', (tester) async {
    await mountMath(
        tester,
        const ScientificText('纯中文正文',
            maxLines: 3, overflow: TextOverflow.ellipsis));
    expect(find.text('纯中文正文'), findsOneWidget);
    expect(find.byType(Math), findsNothing);
  });

  testWidgets('invalid TeX falls back to original source', (tester) async {
    await mountMath(
        tester, const ScientificText(r'可读内容 \(\notARealCommand{x}\) 继续。'));
    expect(find.byKey(const ValueKey('math-source-fallback')), findsOneWidget);
    expect(find.text(r'\(\notARealCommand{x}\)'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('inline math follows text scaling exactly once', (tester) async {
    const content = ScientificText(r'中文 \(\frac{n_e}{T_e}\) 结果',
        style: TextStyle(fontSize: 16));
    await mountMath(tester, content);
    final before = tester.getSize(find.byType(ScientificText)).height;
    await mountMath(tester, content, scale: 2, width: 700);
    final after = tester.getSize(find.byType(ScientificText)).height;
    expect(after / before, closeTo(2, .02));
    expect(tester.widget<Math>(find.byType(Math)).textScaleFactor, 1);
    expect(tester.takeException(), isNull);
  });

  testWidgets('display math follows inherited reading scale', (tester) async {
    await mountMath(tester,
        const ScientificText(r'\[E=mc^2\]', style: TextStyle(fontSize: 16)),
        scale: 1.8);
    expect(tester.widget<Math>(find.byType(Math)).textScaleFactor,
        closeTo(1.8, .001));
    expect(tester.takeException(), isNull);
  });

  testWidgets('long inline and display equations scroll on narrow large text',
      (tester) async {
    final formula = List.filled(30, r'\frac{a_i+b_i}{c_i}').join('+');
    await mountMath(tester, ScientificText('中文 \\($formula\\)\n\\[$formula\\]'),
        scale: 2, width: 260);
    final scrolls = tester.widgetList<SingleChildScrollView>(
        find.byKey(const ValueKey('math-horizontal-scroll')));
    expect(scrolls.length, 2);
    expect(scrolls.every((s) => s.scrollDirection == Axis.horizontal), isTrue);
    final scrollable = find.descendant(
        of: find.byKey(const ValueKey('math-horizontal-scroll')).last,
        matching: find.byType(Scrollable));
    final position = tester.state<ScrollableState>(scrollable).position;
    expect(position.maxScrollExtent, greaterThan(0));
    await tester.drag(scrollable, const Offset(-100, 0));
    await tester.pumpAndSettle();
    expect(position.pixels, greaterThan(0));
    expect(tester.takeException(), isNull);
  });

  testWidgets('list preview retains line cap with display delimiters',
      (tester) async {
    await mountMath(
        tester, const ScientificText(r'摘要 $$n_e=10^{20}$$ 中文说明。', maxLines: 3));
    final text = tester.widget<Text>(find
        .descendant(
            of: find.byType(ScientificText), matching: find.byType(Text))
        .first);
    expect(text.maxLines, 3);
    expect(tester.widget<Math>(find.byType(Math)).mathStyle, MathStyle.text);
    expect(tester.takeException(), isNull);
  });
}
