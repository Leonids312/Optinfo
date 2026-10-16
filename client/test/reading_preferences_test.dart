import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:optinfo/api.dart';
import 'package:optinfo/main.dart';
import 'package:optinfo/models.dart';
import 'package:optinfo/reading_preferences.dart';
import 'package:optinfo/screens.dart';
import 'package:shared_preferences/shared_preferences.dart';

class ReadingApi extends Api {
  int chatLoads = 0;
  final article = NewsItem({
    'id': 'reading-preferences-article',
    'title': '阅读偏好测试资讯',
    'summary': '正文、图注和问答共享阅读字号。',
    'section': 'research',
    'kind': 'paper',
    'content': {'background': '这段文字用于验证字号和侧栏宽度。' * 40},
    'figures': [
      {'caption': '用于验证字号的中文图注。', 'url': ''}
    ],
  });
  ReadingApi() : super(ConnectionStore());
  @override
  Future<NewsItem> item(String id) async => article;
  @override
  Future<Json> call(String path,
      {String method = 'GET', Json? body, Map<String, String>? query}) async {
    if (path.endsWith('/chat')) {
      chatLoads++;
      return {'messages': <Json>[], 'status': 'empty'};
    }
    throw StateError('Unexpected reading test request');
  }
}

Future<void> mountReading(
    WidgetTester tester, ReadingApi api, ReadingPreferencesStore preferences,
    {double systemScale = 1}) async {
  await tester.pumpWidget(RepaintBoundary(
      key: const ValueKey('reading-preferences-capture'),
      child: Builder(builder: (context) {
        final app = OptinfoApp(connection: api.connection).build(context)
            as MaterialApp;
        return MaterialApp(
          debugShowCheckedModeBanner: false,
          theme: app.theme,
          builder: (context, child) => MediaQuery(
            data: MediaQuery.of(context)
                .copyWith(textScaler: TextScaler.linear(systemScale)),
            child:
                ReadingPreferencesView(preferences: preferences, child: child!),
          ),
          home: ArticleScreen(api: api, initial: api.article),
        );
      })));
  await tester.pumpAndSettle();
}

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  test('display preferences persist independently and reset to defaults',
      () async {
    final store = ReadingPreferencesStore();
    await store.load();
    await store.update(textScale: 1.4, sidebarWidth: 510);
    final reopened = ReadingPreferencesStore();
    await reopened.load();
    expect(reopened.value.textScale, 1.4);
    expect(reopened.value.sidebarWidth, 510);
    await reopened.reset();
    final reset = ReadingPreferencesStore();
    await reset.load();
    expect(reset.value.textScale, 1);
    expect(reset.value.sidebarWidth, 366);
  });

  test(
      'invalid preferences and narrow windows are bounded without losing the choice',
      () async {
    SharedPreferences.setMockInitialValues({
      ReadingPreferencesStore.storageKey:
          jsonEncode({'text_scale': 100, 'sidebar_width': 900}),
    });
    final store = ReadingPreferencesStore();
    await store.load();
    expect(store.value.textScale, 1.5);
    expect(store.value.sidebarWidth, 520);
    expect(store.value.sidebarWidthFor(1100), 432);
    expect(store.value.sidebarWidthFor(1250), 520);
    expect(store.value.sidebarWidth, 520);
    await store.update(textScale: double.nan, sidebarWidth: double.infinity);
    expect(store.value.textScale, 1);
    expect(store.value.sidebarWidth, 366);
  });

  test('storage failure does not stop immediate reading changes', () async {
    final store = ReadingPreferencesStore(
      preferences: () => Future.error(StateError('Storage unavailable')),
    );
    await store.load();
    await store.update(textScale: 1.2, sidebarWidth: 400);
    expect(store.value.textScale, 1.2);
    expect(store.value.sidebarWidth, 400);
    expect(store.saveFailed, isTrue);
  });

  test('rapid slider changes persist the final values', () async {
    final preferences = ReadingPreferencesStore();
    await Future.wait([
      preferences.update(textScale: 1.05, sidebarWidth: 300),
      preferences.update(textScale: 1.2, sidebarWidth: 420),
      preferences.update(textScale: 1.5, sidebarWidth: 520),
    ]);
    final reopened = ReadingPreferencesStore();
    await reopened.load();
    expect(reopened.value.textScale, 1.5);
    expect(reopened.value.sidebarWidth, 520);
  });

  testWidgets('home has reading controls and applies its text size to the feed',
      (tester) async {
    tester.view.physicalSize = const Size(1400, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final preferences = ReadingPreferencesStore();
    await tester.pumpWidget(OptinfoApp(
      connection: ConnectionStore(),
      preferences: preferences,
    ));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('阅读设置'));
    await tester.pumpAndSettle();
    tester
        .widget<Slider>(find.byKey(const ValueKey('reading-font-slider')))
        .onChanged!(1.35);
    await tester.pumpAndSettle();
    await tester.tap(find.text('完成'));
    await tester.pumpAndSettle();
    final scaler =
        MediaQuery.textScalerOf(tester.element(find.text('连接你的私人资讯库')));
    expect(scaler.scale(16), closeTo(21.6, .01));
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'home remains usable with system scaling after narrowing a window',
      (tester) async {
    tester.view.physicalSize = const Size(360, 850);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final preferences = ReadingPreferencesStore();
    await preferences.update(textScale: 1.5);
    await tester.pumpWidget(MaterialApp(
      builder: (context, child) => MediaQuery(
        data: MediaQuery.of(context)
            .copyWith(textScaler: const TextScaler.linear(2)),
        child: ReadingPreferencesView(preferences: preferences, child: child!),
      ),
      home: HomeScreen(api: Api(ConnectionStore())),
    ));
    await tester.pumpAndSettle();
    expect(find.byTooltip('连接设置').hitTestable(), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'article changes preserve draft while width clamps and recovers on resize',
      (tester) async {
    tester.view.physicalSize = const Size(1400, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final api = ReadingApi();
    final preferences = ReadingPreferencesStore();
    await mountReading(tester, api, preferences, systemScale: 1.2);
    final panel = find.byKey(const ValueKey('article-explore-panel'));
    await tester.ensureVisible(find.byType(TextField));
    await tester.enterText(find.byType(TextField), '保留阅读时的问题草稿');
    await tester.tap(find.byTooltip('阅读设置'));
    await tester.pumpAndSettle();
    tester
        .widget<Slider>(find.byKey(const ValueKey('reading-sidebar-slider')))
        .onChanged!(520);
    tester
        .widget<Slider>(find.byKey(const ValueKey('reading-font-slider')))
        .onChanged!(1.5);
    await tester.pumpAndSettle();
    await tester.tap(find.text('完成'));
    await tester.pumpAndSettle();
    expect(tester.getSize(panel).width, 520);
    expect(
        MediaQuery.textScalerOf(tester.element(find.byType(TextField)))
            .scale(16),
        closeTo(28.8, .01));

    tester.view.physicalSize = const Size(1100, 1000);
    await tester.pumpAndSettle();
    expect(tester.getSize(panel).width, 432);
    final main = find.byKey(const ValueKey('article-scroll'));
    expect(tester.getSize(main).width, greaterThanOrEqualTo(620));
    tester.view.physicalSize = const Size(1400, 1000);
    await tester.pumpAndSettle();
    expect(tester.getSize(panel).width, 520);
    expect(tester.widget<TextField>(find.byType(TextField)).controller!.text,
        '保留阅读时的问题草稿');
    expect(api.chatLoads, 1);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'narrow screens keep a fixed entry with large accessible reading text',
      (tester) async {
    tester.view.physicalSize = const Size(360, 850);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final api = ReadingApi();
    final preferences = ReadingPreferencesStore();
    await preferences.update(textScale: 1.5, sidebarWidth: 520);
    await mountReading(tester, api, preferences, systemScale: 1.5);
    expect(find.byKey(const ValueKey('article-explore-entry')).hitTestable(),
        findsOneWidget);
    await tester.tap(find.byTooltip('阅读设置'));
    await tester.pumpAndSettle();
    expect(
        tester
            .widget<Slider>(
                find.byKey(const ValueKey('reading-sidebar-slider')))
            .onChanged,
        isNull);
    await tester.tap(find.text('完成'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('article-explore-entry')));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.byType(TextField));
    expect(find.byType(TextField).hitTestable(), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
