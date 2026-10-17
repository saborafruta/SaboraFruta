package br.com.ited.forcavendas.sync

import android.content.Context
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import br.com.ited.forcavendas.data.ApiException
import br.com.ited.forcavendas.data.ItedRepository
import br.com.ited.forcavendas.data.NetworkException
import br.com.ited.forcavendas.data.SessionStore
import java.util.concurrent.TimeUnit

class ItedSyncWorker(
    appContext: Context,
    params: WorkerParameters,
) : CoroutineWorker(appContext, params) {
    override suspend fun doWork(): Result {
        val repository = ItedRepository(SessionStore(applicationContext))
        val session = repository.savedSession() ?: return Result.success()
        return try {
            val summary = repository.sync(session)
            if (summary.pendingAfter > 0) Result.retry() else Result.success()
        } catch (_: NetworkException) {
            Result.retry()
        } catch (error: ApiException) {
            if (error.statusCode == 401) Result.failure() else Result.retry()
        } catch (_: Exception) {
            Result.retry()
        }
    }

    companion object {
        private const val UNIQUE_WORK = "ited-periodic-sync"

        fun schedule(context: Context) {
            val constraints = Constraints.Builder()
                .setRequiredNetworkType(NetworkType.CONNECTED)
                .build()
            val work = PeriodicWorkRequestBuilder<ItedSyncWorker>(15, TimeUnit.MINUTES)
                .setConstraints(constraints)
                .build()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(
                UNIQUE_WORK,
                ExistingPeriodicWorkPolicy.UPDATE,
                work,
            )
        }
    }
}
