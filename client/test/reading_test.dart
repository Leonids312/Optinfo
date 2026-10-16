import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:optinfo/api.dart';
import 'package:optinfo/models.dart';
import 'package:optinfo/screens.dart';
import 'package:shared_preferences/shared_preferences.dart';

class LifecycleApi extends Api {
  final List<Map<String, String>> queries = [];
  final String editionStatus;
  final List<Json> items;
  LifecycleApi({this.editionStatus = 'ready', this.items = const []})
      : super(ConnectionStore()
          ..base = 'https://example.invalid'
          ..token = 'test-only');
  @override
  Future<Json> call(String path,
      {String method = 'GET', Json? body, Map<String, String>? query}) async {
    if (path == '/api/bootstrap') {
      return {'sections': [], 'topics': [], 'journals': []};
    }
    if (path == '/api/briefing') {
      queries.add(Map<String, String>.from(query!));
      return {
        'date': query['date'],
        'items': items
            .where((item) =>
                (query['section'] ?? '').isEmpty ||
                item['section'] == query['section'])
            .toList(),
        'status': editionStatus,
      };
    }
    throw StateError('Unexpected test API path: $path');
  }
}

class TestApi extends Api {
  TestApi()
      : super(ConnectionStore()
          ..base = 'https://example.invalid'
          ..token = 'test-only');
  @override
  Future<Json> call(String path,
      {String method = 'GET', Json? body, Map<String, String>? query}) async {
    if (path.endsWith('/chat') && method == 'POST') throw ApiFailure('模型尚未配置');
    if (path.endsWith('/chat')) {
      return {
        'session_id': null,
        'expires_at': null,
        'messages': [],
        'status': 'empty'
      };
    }
    throw StateError('Unexpected test API path: $path');
  }
}

void main() {
  for (final (status, expectedTitle) in [
    ('ready', '今天暂无新内容'),
    ('not_generated', '今天的简报还未就绪'),
  ]) {
    testWidgets('empty $status edition displays its actual generation state',
        (tester) async {
      SharedPreferences.setMockInitialValues({});
      tester.view.physicalSize = const Size(800, 1000);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      await tester.pumpWidget(MaterialApp(
          home: HomeScreen(api: LifecycleApi(editionStatus: status))));
      await tester.pumpAndSettle();
      expect(find.text(expectedTitle), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets(
      'partial edition renders each article once and empty sections remain distinct from missing editions',
      (tester) async {
    SharedPreferences.setMockInitialValues({});
    tester.view.physicalSize = const Size(800, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final api = LifecycleApi(editionStatus: 'partial', items: [
      {
        'id': 'test-paper',
        'title': '一条测试研究资讯',
        'section': 'research',
        'kind': 'paper',
        'summary': '仅用于界面测试。'
      },
    ]);
    await tester.pumpWidget(MaterialApp(home: HomeScreen(api: api)));
    await tester.pumpAndSettle();
    expect(find.text('已生成部分内容，后续会自动补齐。'), findsOneWidget);
    expect(find.byType(NewsCard), findsOneWidget);
    await tester.tap(find.widgetWithText(ChoiceChip, '重大事件'));
    await tester.pumpAndSettle();
    expect(find.text('暂无达到筛选标准的重大事件'), findsOneWidget);
    expect(find.text('今天的简报还未就绪'), findsNothing);
    await tester.tap(find.widgetWithText(ChoiceChip, '产业动态'));
    await tester.pumpAndSettle();
    expect(find.text('该分区暂无新内容'), findsOneWidget);
    await tester.tap(find.widgetWithText(ChoiceChip, '研究进展'));
    await tester.pumpAndSettle();
    expect(find.byType(NewsCard), findsOneWidget);
    await tester.tap(find.text('历史'));
    await tester.pumpAndSettle();
    expect(find.byType(NewsCard), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'resume keeps a historical date but today requests the current Shanghai day',
      (tester) async {
    SharedPreferences.setMockInitialValues({});
    tester.view.physicalSize = const Size(800, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final api = LifecycleApi();
    final today = shanghaiToday();
    final past = DateTime.parse(today).subtract(const Duration(days: 1));
    final pastText = past.toIso8601String().substring(0, 10);
    await tester.pumpWidget(MaterialApp(home: HomeScreen(api: api)));
    await tester.pumpAndSettle();
    expect(api.queries.last['date'], today);
    await tester.tap(find.text('历史'));
    await tester.pumpAndSettle();
    await tester.tap(find.text(today));
    await tester.pumpAndSettle();
    tester
        .widget<CalendarDatePicker>(find.byType(CalendarDatePicker))
        .onDateChanged(past);
    await tester.pumpAndSettle();
    await tester.tap(find.text('OK'));
    await tester.pumpAndSettle();
    expect(api.queries.last['date'], pastText);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.pumpAndSettle();
    expect(api.queries.last['date'], pastText);
    await tester.tap(find.text('今日'));
    await tester.pumpAndSettle();
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.pumpAndSettle();
    expect(api.queries.last['date'], shanghaiToday());
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'an intentionally empty pending selection does not restore today interests',
      (tester) async {
    tester.view.physicalSize = const Size(800, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: InterestsSheet(api: TestApi(), bootstrap: {
      'sections': [
        {'id': 'research', 'label': '研究进展'}
      ],
      'topics': [
        {
          'id': 'nanophotonics',
          'label': '纳米光子学',
          'section': 'research',
          'kind': '研究方向'
        }
      ],
      'active_topics': ['nanophotonics'],
      'pending_topics': <String>[],
      'effective_date': '2026-10-17',
    }))));
    expect(
        tester.widget<FilterChip>(find.byType(FilterChip)).selected, isFalse);
    expect(find.text('已选 0 个领域'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('a failed question preserves the input without inventing a reply',
      (tester) async {
    tester.view.physicalSize = const Size(360, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: SingleChildScrollView(
                child: ChatPanel(api: TestApi(), itemId: 'test-item')))));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField), '这个实验有哪些局限？');
    await tester.tap(find.text('发送问题'));
    await tester.pumpAndSettle();
    expect(tester.widget<TextField>(find.byType(TextField)).controller!.text,
        '这个实验有哪些局限？');
    expect(find.textContaining('模型尚未配置'), findsOneWidget);
    expect(find.text('DeepSeek'), findsNothing);
    expect(tester.takeException(), isNull);
  });
}
