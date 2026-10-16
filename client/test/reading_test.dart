import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:optinfo/api.dart';
import 'package:optinfo/models.dart';
import 'package:optinfo/screens.dart';

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
