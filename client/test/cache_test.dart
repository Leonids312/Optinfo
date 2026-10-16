import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:optinfo/api.dart';
import 'package:optinfo/cache.dart';
import 'package:optinfo/models.dart';
import 'package:optinfo/screens.dart';
import 'package:shared_preferences/shared_preferences.dart';

class TestClock {
  DateTime value = DateTime.utc(2026, 10, 16, 4);
  DateTime now() => value;
}

class CachingApi extends Api {
  final List<(String, Map<String, String>)> requests = [];
  Future<Json> Function(String, String, Json?, Map<String, String>)? handler;
  bool offline = false;
  CachingApi(TestClock time, {ConnectionStore? connection, super.cache})
      : super(
            connection ??
                (ConnectionStore()
                  ..base = 'https://test.invalid'
                  ..token = 'private-test-token'),
            clock: time.now);
  @override
  Future<Json> call(String path,
      {String method = 'GET', Json? body, Map<String, String>? query}) async {
    final filters = Map<String, String>.from(query ?? {});
    requests.add((path, filters));
    if (offline) throw ApiFailure('离线测试');
    if (handler != null) return handler!(path, method, body, filters);
    if (path == '/api/bootstrap') {
      return {'topics': [], 'sections': [], 'journals': []};
    }
    if (path == '/api/briefing') {
      return edition(filters, filters['q'] ?? '今日研究');
    }
    if (method == 'PUT') return {'bookmarked': body!['bookmarked']};
    throw StateError('Unexpected endpoint: $path');
  }

  int get feedCalls => requests.where((r) => r.$1 == '/api/briefing').length;
}

Json edition(Map<String, String> filters, String title,
        {bool marked = false}) =>
    {
      'date': filters['date'] ?? '2026-10-16',
      'status': 'ready',
      'items': [
        {
          'id': 'paper-one',
          'title': title,
          'summary': '缓存测试资讯',
          'section': 'research',
          'kind': 'paper',
          'bookmarked': marked
        },
      ],
    };

void setupView(WidgetTester tester) {
  tester.view.physicalSize = const Size(800, 1000);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
}

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  testWidgets(
      'tab revisits retain search and render immediately without another request or spinner',
      (tester) async {
    setupView(tester);
    final api = CachingApi(TestClock());
    await tester.pumpWidget(MaterialApp(home: HomeScreen(api: api)));
    await tester.pumpAndSettle();
    expect(api.feedCalls, 1);
    await tester.tap(find.text('历史'));
    await tester.pumpAndSettle();
    expect(api.feedCalls, 1);
    await tester.enterText(find.byType(TextField), '激光');
    await tester.testTextInput.receiveAction(TextInputAction.search);
    await tester.pumpAndSettle();
    expect(find.text('激光'), findsWidgets);
    expect(api.feedCalls, 2);
    await tester.tap(find.text('今日'));
    await tester.pump();
    expect(find.text('今日研究'), findsOneWidget);
    expect(find.byType(LinearProgressIndicator), findsNothing);
    await tester.tap(find.text('历史'));
    await tester.pump();
    expect(tester.widget<TextField>(find.byType(TextField)).controller!.text,
        '激光');
    expect(find.byType(NewsCard), findsOneWidget);
    expect(find.byType(LinearProgressIndicator), findsNothing);
    await tester.pumpAndSettle();
    expect(api.feedCalls, 2);
    await tester.tap(find.byTooltip('刷新内容'));
    await tester.pumpAndSettle();
    expect(api.feedCalls, 3);
  });

  testWidgets('persisted stale briefing opens offline after a new app instance',
      (tester) async {
    setupView(tester);
    final time = TestClock();
    final first = CachingApi(time);
    await first.fetch(first.query('/api/briefing',
        query: {'date': first.today, 'bookmarked': 'false'}));
    time.value = time.value.add(const Duration(minutes: 6));
    final reopened = CachingApi(time)..offline = true;
    await tester.pumpWidget(MaterialApp(home: HomeScreen(api: reopened)));
    await tester.pumpAndSettle();
    expect(find.text('今日研究'), findsOneWidget);
    expect(find.textContaining('显示最近加载的内容'), findsOneWidget);
    expect(find.byType(LinearProgressIndicator), findsNothing);
    final saved = (await SharedPreferences.getInstance())
        .getString(LocalQueryCache.storageKey)!;
    expect(saved, isNot(contains('private-test-token')));
  });

  testWidgets('a late old query does not replace the selected section',
      (tester) async {
    setupView(tester);
    final api = CachingApi(TestClock());
    final slow = Completer<Json>();
    api.handler = (path, method, body, query) async {
      if (path == '/api/bootstrap') {
        return {'topics': [], 'sections': [], 'journals': []};
      }
      if (query['section'] == 'industry') return slow.future;
      return edition(query, query['section'] == 'world' ? '新重大事件' : '今日研究')
        ..['items'][0]['section'] = query['section'] ?? 'research';
    };
    await tester.pumpWidget(MaterialApp(home: HomeScreen(api: api)));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(ChoiceChip, '产业动态'));
    await tester.pump();
    await tester.tap(find.widgetWithText(ChoiceChip, '重大事件'));
    await tester.pumpAndSettle();
    expect(find.text('新重大事件'), findsOneWidget);
    slow.complete(edition({'date': api.today}, '旧产业结果')
      ..['items'][0]['section'] = 'industry');
    await tester.pumpAndSettle();
    expect(find.text('旧产业结果'), findsNothing);
    expect(find.text('新重大事件'), findsOneWidget);
  });

  test(
      'query filters, fixed historical dates, account changes and chat remain isolated',
      () async {
    final time = TestClock();
    final api = CachingApi(time);
    final historical =
        api.query('/api/briefing', query: {'date': '2026-10-10', 'q': '激光'});
    await api.fetch(historical);
    time.value = time.value.add(const Duration(days: 1));
    expect(
        (await api.cached(api.query('/api/briefing',
                query: {'q': '激光', 'date': '2026-10-10'})))
            ?.data['items'],
        isNotEmpty);
    expect(
        await api.cached(api
            .query('/api/briefing', query: {'q': '芯片', 'date': '2026-10-10'})),
        isNull);
    final old = Completer<Json>();
    api.handler = (path, method, body, query) => old.future;
    final oldRequest = api.query('/api/briefing', query: {'date': api.today});
    final pending = api.fetch(oldRequest);
    final rejected = expectLater(pending, throwsA(isA<ApiFailure>()));
    await Future<void>.delayed(Duration.zero);
    api.connection.token = 'another-private-token';
    final newRequest = api.query('/api/briefing', query: {'date': api.today});
    expect(await api.cached(newRequest), isNull);
    old.complete(edition({'date': api.today}, '其他账号内容'));
    await rejected;
    expect(api.peek(newRequest), isNull);
    await expectLater(api.fetch(api.query('/api/items/paper-one/chat')),
        throwsA(isA<ApiFailure>()));
    final serialized = (await SharedPreferences.getInstance())
        .getString(LocalQueryCache.storageKey)!;
    expect(serialized, isNot(contains('其他账号内容')));
  });

  testWidgets(
      'Shanghai rollover never relabels yesterday cached items as today',
      (tester) async {
    setupView(tester);
    final time = TestClock()..value = DateTime.utc(2026, 10, 16, 15, 59);
    final api = CachingApi(time);
    await tester.pumpWidget(MaterialApp(home: HomeScreen(api: api)));
    await tester.pumpAndSettle();
    expect(find.text('今日研究'), findsOneWidget);
    time.value = DateTime.utc(2026, 10, 16, 16, 1);
    api.offline = true;
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.pumpAndSettle();
    expect(find.text('今日研究'), findsNothing);
    expect(api.requests.last.$2['date'], '2026-10-17');
  });

  test('bookmark wins over an earlier GET and persists across a new instance',
      () async {
    final time = TestClock();
    final api = CachingApi(time);
    final query = api.query('/api/briefing', query: {'date': api.today});
    await api.fetch(query);
    final response = Completer<Json>();
    api.handler = (path, method, body, filters) async =>
        method == 'PUT' ? {'bookmarked': true} : response.future;
    final pending = api.fetch(query);
    await Future<void>.delayed(Duration.zero);
    await api.bookmark('paper-one', true);
    response.complete(edition({'date': api.today}, '今日研究', marked: false));
    expect((await pending)['items'][0]['bookmarked'], isTrue);
    final reopened = CachingApi(time);
    expect(
        (await reopened.cached(
                reopened.query('/api/briefing', query: {'date': api.today})))!
            .data['items'][0]['bookmarked'],
        isTrue);
  });

  test('cache is bounded and corrupt disk data does not stop a fresh read',
      () async {
    SharedPreferences.setMockInitialValues(
        {LocalQueryCache.storageKey: 'broken json'});
    final time = TestClock();
    final api = CachingApi(time);
    for (var index = 0; index < 55; index++) {
      await api.fetch(api.query('/api/briefing', query: {'q': 'topic-$index'}));
    }
    final stored = (await SharedPreferences.getInstance())
        .getString(LocalQueryCache.storageKey)!;
    expect((jsonDecode(stored)['entries'] as List).length,
        lessThanOrEqualTo(LocalQueryCache.maxEntries));
    expect(utf8.encode(stored).length,
        lessThanOrEqualTo(LocalQueryCache.maxBytes));
    time.value = time.value.add(const Duration(days: 31));
    expect(
        await api.cached(api.query('/api/briefing', query: {'q': 'topic-54'})),
        isNull);
  });
}
