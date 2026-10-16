import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:optinfo/api.dart';
import 'package:optinfo/cache.dart';
import 'package:optinfo/models.dart';
import 'package:optinfo/screens.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Keep the GET suspended after the cache snapshot has been written, as a slow
/// platform storage acknowledgement can do on a real device.
class DelayedPutCache extends LocalQueryCache {
  final stored = Completer<void>();
  final release = Completer<void>();

  @override
  Future<void> put(QueryRequest request, Json data) async {
    await super.put(request, data);
    stored.complete();
    await release.future;
  }
}

class DiskRaceApi extends Api {
  DiskRaceApi(LocalQueryCache cache)
      : super(
          ConnectionStore()
            ..base = 'https://example.invalid'
            ..token = 'test-only-first-account',
          cache: cache,
        );

  @override
  Future<Json> call(String path,
      {String method = 'GET', Json? body, Map<String, String>? query}) async {
    if (path == '/api/items/paper-1/bookmark' && method == 'PUT') {
      return {'bookmarked': body!['bookmarked']};
    }
    if (path == '/api/briefing' && method == 'GET') {
      return {
        'date': '2026-10-16',
        'status': 'ready',
        'items': [
          {'id': 'paper-1', 'title': 'Test article', 'bookmarked': false},
        ],
      };
    }
    throw StateError('Unexpected fixture API request');
  }
}

class ScrollCacheApi extends Api {
  int feedCalls = 0;
  ScrollCacheApi()
      : super(ConnectionStore()
          ..base = 'https://example.invalid'
          ..token = 'test-only-scroll-account');

  @override
  Future<Json> call(String path,
      {String method = 'GET', Json? body, Map<String, String>? query}) async {
    if (path == '/api/bootstrap') {
      return {'topics': [], 'sections': [], 'journals': []};
    }
    if (path == '/api/briefing') {
      feedCalls++;
      return {
        'date': query!['date'],
        'status': 'ready',
        'items': List.generate(
          18,
          (index) => {
            'id': 'scroll-paper-$index',
            'title': 'Research article $index',
            'summary': 'Content for reading-position verification.',
            'section': 'research',
            'kind': 'paper',
            'bookmarked': false,
          },
        ),
      };
    }
    throw StateError('Unexpected scroll fixture API request');
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUp(() => SharedPreferences.setMockInitialValues({}));

  test('account change during persistence rejects the old GET result',
      () async {
    final cache = DelayedPutCache();
    final api = DiskRaceApi(cache);
    final query = api.query('/api/briefing', query: {'date': '2026-10-16'});
    final pending = api.fetch(query);
    await cache.stored.future;

    api.connection.token = 'test-only-second-account';
    final rejected = expectLater(pending, throwsA(isA<ApiFailure>()));
    cache.release.complete();
    await rejected;
    expect(api.peek(query), isNull);
    expect(await api.cached(api.query('/api/briefing', query: query.query)),
        isNull);
  });

  test('bookmark during persistence survives GET completion and restart',
      () async {
    final cache = DelayedPutCache();
    final api = DiskRaceApi(cache);
    final query = api.query('/api/briefing', query: {'date': '2026-10-16'});
    final pending = api.fetch(query);
    await cache.stored.future;

    await api.bookmark('paper-1', true);
    cache.release.complete();
    expect(objects((await pending)['items']).single['bookmarked'], isTrue);
    expect(
        objects(api.peek(query)!.data['items']).single['bookmarked'], isTrue);

    final restarted = LocalQueryCache();
    await restarted.ready(api.cacheOwner);
    expect(objects(restarted.peek(query)!.data['items']).single['bookmarked'],
        isTrue);
  });

  testWidgets(
      'today and history independently restore cached reading positions',
      (tester) async {
    tester.view.physicalSize = const Size(800, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final api = ScrollCacheApi();
    await tester.pumpWidget(MaterialApp(home: HomeScreen(api: api)));
    await tester.pumpAndSettle();

    Finder feed(int page) => find.byKey(PageStorageKey('optinfo-feed-$page'));
    double offset(int page) => tester
        .state<ScrollableState>(find
            .descendant(of: feed(page), matching: find.byType(Scrollable))
            .first)
        .position
        .pixels;

    await tester.drag(feed(0), const Offset(0, -700));
    await tester.pumpAndSettle();
    final todayOffset = offset(0);
    expect(todayOffset, greaterThan(500));

    await tester.tap(find.text('历史'));
    await tester.pumpAndSettle();
    await tester.drag(feed(1), const Offset(0, -1200));
    await tester.pumpAndSettle();
    final historyOffset = offset(1);
    expect(historyOffset, greaterThan(todayOffset + 200));

    await tester.tap(find.text('今日'));
    await tester.pumpAndSettle();
    expect(offset(0), closeTo(todayOffset, 1));
    await tester.tap(find.text('历史'));
    await tester.pumpAndSettle();
    expect(offset(1), closeTo(historyOffset, 1));
    expect(api.feedCalls, 1);
    expect(tester.takeException(), isNull);
  });
}
