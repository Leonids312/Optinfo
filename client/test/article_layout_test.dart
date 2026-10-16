import 'dart:async';
import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:optinfo/api.dart';
import 'package:optinfo/main.dart';
import 'package:optinfo/models.dart';
import 'package:optinfo/screens.dart';

class ArticleLayoutApi extends Api {
  int chatLoads = 0;
  int questions = 0;
  Completer<Json>? pendingQuestion;
  final NewsItem article = NewsItem({
    'id': 'layout-test-article',
    'title': '用于布局验证的研究资讯',
    'kind': 'paper',
    'section': 'research',
    'summary': '这段文字只用于界面测试，不是实际推送。',
    'content': {
      for (final key in ['background', 'approach', 'results', 'assessment'])
        key: List.filled(24, '这是用于验证独立滚动的长篇研究内容，包含研究背景和证据边界。').join('\n'),
    },
    'tags': List.generate(18, (index) => '测试词条 $index'),
  });

  ArticleLayoutApi() : super(ConnectionStore());

  @override
  Future<NewsItem> item(String id) async => article;

  @override
  Future<Json> call(String path,
      {String method = 'GET', Json? body, Map<String, String>? query}) async {
    if (!path.endsWith('/chat')) throw StateError('Unexpected test request');
    if (method == 'POST') {
      questions += 1;
      return pendingQuestion!.future;
    }
    chatLoads += 1;
    return {
      'status': 'active',
      'messages': [
        {'role': 'assistant', 'content': '已保存的会话内容。'}
      ],
    };
  }
}

Future<void> mountArticle(WidgetTester tester, ArticleLayoutApi api,
    {required Size size, double textScale = 1}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(tester.view.resetViewInsets);
  if (Platform.environment['OPTINFO_CAPTURE_LAYOUT'] == '1') {
    final loader = FontLoader('NotoSansSC')
      ..addFont(rootBundle.load('assets/fonts/NotoSansSC-Variable.ttf'));
    await loader.load();
    final icons = FontLoader('MaterialIcons')
      ..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf'));
    await icons.load();
  }
  await tester.pumpWidget(RepaintBoundary(
    key: const ValueKey('article-test-capture'),
    child: Builder(builder: (context) {
      final app =
          OptinfoApp(connection: api.connection).build(context) as MaterialApp;
      return MaterialApp(
        debugShowCheckedModeBanner: false,
        theme: app.theme,
        builder: (context, child) => MediaQuery(
          data: MediaQuery.of(context)
              .copyWith(textScaler: TextScaler.linear(textScale)),
          child: child!,
        ),
        home: ArticleScreen(api: api, initial: api.article),
      );
    }),
  ));
  await tester.pumpAndSettle();
}

Future<void> captureLayout(WidgetTester tester, String name) async {
  if (Platform.environment['OPTINFO_CAPTURE_LAYOUT'] != '1') return;
  final boundary = tester.renderObject<RenderRepaintBoundary>(
      find.byKey(const ValueKey('article-test-capture')));
  final shadows = debugDisableShadows;
  try {
    debugDisableShadows = false;
    boundary.markNeedsPaint();
    await tester.pump();
    await tester.runAsync(() async {
      final image = await boundary.toImage();
      final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
      final file = File('../.ops/$name.png');
      await file.parent.create(recursive: true);
      await file.writeAsBytes(bytes!.buffer.asUint8List());
      image.dispose();
    });
  } finally {
    debugDisableShadows = shadows;
  }
}

void main() {
  testWidgets(
      'desktop article scroll leaves independently scrollable aside anchored',
      (tester) async {
    final api = ArticleLayoutApi();
    await mountArticle(tester, api, size: const Size(1280, 800));
    final main = find.byKey(const ValueKey('article-scroll'));
    final aside = find.byKey(const ValueKey('article-explore-panel'));
    final asideScroll = find.byKey(const ValueKey('article-explore-scroll'));
    final position = tester.getRect(aside);
    await tester.drag(main, const Offset(0, -850));
    await tester.pumpAndSettle();
    expect(tester.getRect(aside), position);
    expect(find.text('继续探究').hitTestable(), findsOneWidget);
    await captureLayout(tester, 'article-desktop-sticky');
    final controller = tester.widget<SingleChildScrollView>(main).controller!;
    expect(controller.offset, greaterThan(300));
    final mainOffset = controller.offset;
    await tester.drag(asideScroll, const Offset(0, -600));
    await tester.pumpAndSettle();
    expect(controller.offset, mainOffset);
    expect(tester.widget<SingleChildScrollView>(asideScroll).controller!.offset,
        greaterThan(0));
    expect(find.text('探究与词条').hitTestable(), findsOneWidget);
    await tester.tap(find.widgetWithText(TextButton, '词条'));
    await tester.pumpAndSettle();
    expect(find.text('相关词条').hitTestable(), findsOneWidget);
    await tester.tap(find.widgetWithText(TextButton, '提问'));
    await tester.pumpAndSettle();
    expect(find.byType(TextField).hitTestable(), findsOneWidget);
    expect(api.chatLoads, 1);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'mobile fixed entry preserves draft across close resize and keyboard',
      (tester) async {
    final api = ArticleLayoutApi();
    await mountArticle(tester, api, size: const Size(390, 800), textScale: 1.6);
    final entry = find.byKey(const ValueKey('article-explore-entry'));
    await tester.drag(
        find.byKey(const ValueKey('article-scroll')), const Offset(0, -900));
    await tester.pumpAndSettle();
    expect(entry.hitTestable(), findsOneWidget);
    await captureLayout(tester, 'article-mobile-entry');
    await tester.tap(entry);
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.byType(TextField));
    await tester.enterText(find.byType(TextField), '保留这段尚未发送的问题');
    await tester.tap(find.byKey(const ValueKey('article-explore-close')));
    await tester.pumpAndSettle();
    expect(entry.hitTestable(), findsOneWidget);
    await tester.tap(entry);
    await tester.pumpAndSettle();
    expect(tester.widget<TextField>(find.byType(TextField)).controller!.text,
        '保留这段尚未发送的问题');
    tester.view.physicalSize = const Size(1300, 800);
    await tester.pumpAndSettle();
    expect(tester.widget<TextField>(find.byType(TextField)).controller!.text,
        '保留这段尚未发送的问题');
    tester.view.physicalSize = const Size(360, 640);
    tester.view.viewInsets = const FakeViewPadding(bottom: 260);
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('发送问题'));
    await tester.pumpAndSettle();
    expect(find.text('发送问题').hitTestable(), findsOneWidget);
    expect(tester.getBottomLeft(find.text('发送问题')).dy, lessThan(380));
    expect(api.chatLoads, 1);
    expect(find.byType(ChatPanel, skipOffstage: false), findsOneWidget);
    expect(tester.takeException(), isNull);
    await captureLayout(tester, 'article-mobile-keyboard');
  });

  testWidgets(
      'closing and resizing during a question keeps one request and its answer',
      (tester) async {
    final api = ArticleLayoutApi()..pendingQuestion = Completer<Json>();
    await mountArticle(tester, api, size: const Size(390, 800));
    await tester.tap(find.byKey(const ValueKey('article-explore-entry')));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.byType(TextField));
    await tester.enterText(find.byType(TextField), '这项研究有哪些边界？');
    await tester.ensureVisible(find.text('发送问题'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('发送问题'));
    await tester.pump();
    await tester.tap(find.byKey(const ValueKey('article-explore-close')));
    await tester.pump();
    tester.view.physicalSize = const Size(1280, 800);
    await tester.pump();
    api.pendingQuestion!.complete({
      'messages': [
        {'role': 'user', 'content': '这项研究有哪些边界？'},
        {'role': 'assistant', 'content': '这是同一次请求返回的测试回答。'}
      ],
    });
    await tester.pumpAndSettle();
    expect(find.text('这是同一次请求返回的测试回答。'), findsOneWidget);
    expect(api.questions, 1);
    expect(api.chatLoads, 1);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'small landscape keyboard leaves accessible compact controls and back closes panel',
      (tester) async {
    final api = ArticleLayoutApi();
    await mountArticle(tester, api, size: const Size(740, 360), textScale: 2);
    await tester.tap(find.byKey(const ValueKey('article-explore-entry')));
    await tester.pumpAndSettle();
    tester.view.viewInsets = const FakeViewPadding(bottom: 180);
    await tester.pumpAndSettle();
    expect(find.byTooltip('提问').hitTestable(), findsOneWidget);
    expect(find.byTooltip('词条').hitTestable(), findsOneWidget);
    expect(find.byKey(const ValueKey('article-explore-close')).hitTestable(),
        findsOneWidget);
    expect(tester.takeException(), isNull);
    tester.view.resetViewInsets();
    await tester.binding.handlePopRoute();
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('article-explore-entry')).hitTestable(),
        findsOneWidget);
    expect(api.chatLoads, 1);
    expect(tester.takeException(), isNull);
  });
}
